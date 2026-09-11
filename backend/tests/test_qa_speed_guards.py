from __future__ import annotations

import os
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace

import httpx
import pytest
from ragkb.adapters.model_http import HttpxJsonTransport, OpenAICompatibleClaimVerifier
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.application.acceptance_budget import acceptance_budget, reserve_call
from ragkb.application.cancellation import cancellation_scope, check_cancelled
from ragkb.application.condition_scheduler import run_condition_batches
from ragkb.application.qa_performance import performance_report, performance_scope
from ragkb.application.reading_scope import ReadingOptions, reading_scope
from ragkb.domain.answer_conditions import (
    ConditionCheckError,
    condition_requirements,
    validate_condition_checks,
)
from ragkb.domain.errors import IngestionCancelled, InvalidProviderResponse, ProviderTimeout
from ragkb.domain.rag import AnswerStatus
from ragkb.infrastructure.exact_answer_reuse import ExactAnswerReuse
from ragkb.infrastructure.qa_snapshot import MySQLQASnapshot
from test_batched_verifier import ReviewTransport, sample
from test_model_http_adapters import _settings
from test_trusted_qa import _evidence, _service


class Cache:
    def __init__(self):
        self.values = {}

    def get_json(self, namespace, key):
        return deepcopy(self.values.get((namespace, key)))

    def set_json(self, namespace, key, value, ttl):
        self.values[namespace, key] = deepcopy(value)


def cached_service(tmp_path):
    provider = SyntheticEvidenceProvider((_evidence(),))
    service, repository, signer = _service(tmp_path, provider)
    snapshot = ["all-published-v1"]
    service.result_reuse = ExactAnswerReuse(
        Cache(), lambda *args: snapshot[0], config_revision="rules-v1", ttl_seconds=3600
    )
    return service, provider, snapshot, repository, signer


def test_exact_hit_skips_models_but_issues_fresh_authorized_references(tmp_path):
    service, provider, snapshot, repository, signer = cached_service(tmp_path)
    first = service.ask("保修期多久？", "tenant", "user")
    service.generator.fail = True
    second = service.ask("保修期多久？", "tenant", "user")
    assert first.verified and second.verified and first.answer == second.answer
    assert first.rag_run_id != second.rag_run_id
    assert first.citations[0].source_url != second.citations[0].source_url
    assert repository.get_package(second.rag_run_id).query == "保修期多久？"
    assert repository.get_package(second.rag_run_id).retrieval_queries == ()
    assert second.coverage_report["retrieval_budget"]["used"] == 0
    assert second.coverage_report["budget"]["model_calls"] == 0
    assert any(e.get("outcome") == "hit" for e in second.coverage_report["performance"]["events"])


def test_new_uncited_conflict_and_permission_revocation_block_cached_release(tmp_path):
    service, provider, snapshot, _, _ = cached_service(tmp_path)
    assert service.ask("保修期多久？", "tenant", "user").verified
    snapshot[0] = "new-uncited-conflict-document"
    provider.conflict_detected = True
    assert service.ask("保修期多久？", "tenant", "user").status is AnswerStatus.CONFLICTING_EVIDENCE
    snapshot[0] = "all-published-v1"
    service.permission.allowed = False
    result = service.ask("保修期多久？", "tenant", "user")
    assert not result.verified and result.answer is None and not result.citations


@pytest.mark.parametrize(
    "change",
    [
        "question",
        "user",
        "tenant",
        "space",
        "acl",
        "clearance",
        "mode",
        "files",
        "rules",
        "snapshot",
    ],
)
def test_exact_cache_scope_and_rule_changes_cannot_hit(tmp_path, change):
    service, _, snapshot, _, _ = cached_service(tmp_path)
    question, tenant, user, scope = "保修期多久？", "tenant", "user", {}
    assert service.ask(question, tenant, user).verified
    service.generator.fail = True
    if change == "question":
        question += "请说一下"
    if change == "user":
        user = "other"
    if change == "tenant":
        tenant = "other"
    if change == "space":
        scope["space_id"] = "other"
    if change == "acl":
        scope["subject_scope_tokens"] = ("auth-revision:2",)
    if change == "clearance":
        scope["clearance_level"] = 1
    if change == "rules":
        service.result_reuse.config_revision = "rules-v2"
    if change == "snapshot":
        snapshot[0] = "new-parser-image-or-publication"
    with reading_scope(
        ReadingOptions(
            mode="fact" if change == "mode" else "auto",
            document_ids=("d",) if change == "files" else (),
        )
    ):
        assert not service.ask(question, tenant, user, **scope).verified


def test_acceptance_and_relative_time_never_reuse(tmp_path):
    service, _, _, _, _ = cached_service(tmp_path)
    assert service.ask("保修期多久？", "tenant", "user").verified
    service.generator.fail = True
    with acceptance_budget(lambda: None, lambda _: None):
        assert not service.ask("保修期多久？", "tenant", "user").verified
    assert service.result_reuse.key("今天的限制？", "tenant", "user") is None


def test_snapshot_changes_between_lookup_and_release_cannot_hit(tmp_path):
    service, provider, snapshot, _, _ = cached_service(tmp_path)
    assert service.ask("保修期多久？", "tenant", "user").verified
    values = iter([snapshot[0], "new-conflict"])
    service.result_reuse.snapshot = lambda *args: next(values, "new-conflict")
    provider.conflict_detected = True
    assert service.ask("保修期多久？", "tenant", "user").status is AnswerStatus.CONFLICTING_EVIDENCE


def test_queue_consumes_http_budget_and_expired_queue_sends_no_request(tmp_path):
    settings, _ = _settings(tmp_path)
    settings = settings.model_copy(
        update={"model_account_limit_enabled": False, "model_usage_enabled": False}
    )

    class Account:
        redis = type("Redis", (), {"close": lambda self: None})()

        @contextmanager
        def reserve(self, *args):
            time.sleep(0.04)
            yield None

    class Client:
        def __init__(self):
            self.timeouts = []

        def post(self, url, **kwargs):
            self.timeouts.append(kwargs["timeout"].read)
            return httpx.Response(200, json={"usage": {"total_tokens": 10}})

        def close(self):
            pass

    with HttpxJsonTransport(settings) as transport, performance_scope():
        transport._client.close()
        transport._client = client = Client()
        transport._account = Account()
        transport._account_post(
            "http://test", headers={}, json={"model": "test"}, timeout=httpx.Timeout(0.12)
        )
        assert 0 < client.timeouts[0] < 0.09
        with pytest.raises(ProviderTimeout):
            transport._account_post(
                "http://test", headers={}, json={"model": "test"}, timeout=httpx.Timeout(0.01)
            )
        assert len(client.timeouts) == 1
        events = performance_report()["events"]
        assert events[0]["queue_seconds"] >= 0.035 and events[1]["sent"] is False


def test_model_http_400_is_typed_and_never_protocol_repaired(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(20)

    class Reject(ReviewTransport):
        def post_json(self, *args, **kwargs):
            self.calls.append(1)
            raise InvalidProviderResponse(
                "MODEL_PROVIDER_MODEL_UNSUPPORTED", diagnostic={"http_status": 400}
            )

    transport = Reject()
    with pytest.raises(InvalidProviderResponse) as raised:
        OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
            "保修期多久？", draft, sources
        )
    assert len(transport.calls) == 1
    assert raised.value.diagnostic == {
        "http_status": 400,
        "verification_stage": "claims_and_conflicts",
    }


def test_parallel_batches_overlap_preserve_order_and_context():
    barrier = threading.Barrier(3)

    def hook():
        pass

    def run(index):
        assert reserve_call.get() is hook
        barrier.wait(timeout=2)
        return [index]

    with acceptance_budget(hook, lambda _: None):
        assert run_condition_batches([0, 1, 2], run, workers=3) == (0, 1, 2)


def test_parallel_failure_cancels_siblings_and_never_starts_later_batches():
    barrier = threading.Barrier(2)
    started = []

    def run(index):
        started.append(index)
        barrier.wait(timeout=2)
        if index == 0:
            raise ProviderTimeout("MODEL_PROVIDER_TIMEOUT")
        while True:
            check_cancelled()
            time.sleep(0.01)

    with pytest.raises(ProviderTimeout) as raised:
        run_condition_batches(list(range(10)), run, workers=2)
    assert set(started) == {0, 1}
    assert raised.value.diagnostic["batch_number"] == 1


def test_parent_cancellation_reaches_running_batch():
    cancelled = threading.Event()

    def run(index):
        cancelled.set()
        check_cancelled()

    with cancellation_scope(cancelled.is_set), pytest.raises(IngestionCancelled):
        run_condition_batches([1], run, workers=1)


@pytest.mark.parametrize(
    "reason",
    [
        "This rule is unrelated to the question.",
        "The condition is not relevant to the question.",
        "该条件不适用于本题。",
        "This restriction does not apply to the request.",
    ],
)
def test_inconsistent_applicability_requires_correction(reason):
    draft, sources = sample(1)
    required = condition_requirements(sources)
    raw = [
        {"id": "K1", "status": "missing", "applicable": True, "answer_quote": "", "reason": reason}
    ]
    with pytest.raises(ConditionCheckError, match="VERIFIER_CONDITION_VERDICT_INVALID"):
        validate_condition_checks(raw, required, draft, "保修期多久？")


def test_original_flat_heading_nodes_and_condition_anchors_are_preserved():
    from ragkb.document_processing.qa_structure import condition_anchors, prepare_nodes
    from ragkb.domain.documents import CanonicalNode, NodeType, SourceLocator

    nodes = tuple(
        CanonicalNode(
            str(i),
            None,
            kind,
            text,
            text,
            SourceLocator(page=i + 1),
            {"heading_level": 2} if kind is NodeType.HEADING else {},
        )
        for i, (kind, text) in enumerate(
            [
                (NodeType.HEADING, "分类"),
                (NodeType.PARAGRAPH, "第一类。"),
                (NodeType.HEADING, "2.第二类"),
                (NodeType.PARAGRAPH, "仅限室内。"),
            ]
        )
    )
    prepared = prepare_nodes(nodes)
    assert [n.original_text for n in prepared] == [n.original_text for n in nodes]
    assert all(n.metadata["qa_structure"]["heading_boundary_uncertain"] for n in prepared)
    assert prepared[1].metadata["qa_structure"]["next_node_id"] == "2"
    original = _evidence(display_text="仅限室内。", locator=condition_anchors("仅限室内。"))
    assert condition_requirements((original,)) == condition_requirements(
        (replace(original, locator={"page": 1}),)
    )
    changed = replace(original, display_text="不得用于室外。")
    assert condition_requirements((changed,))[0]["source_quote"] == "不得用于室外。"


def test_snapshot_includes_uncited_sources_and_effective_time(monkeypatch):
    from ragkb.domain.retrieval import RetrievalRelease

    rows = [
        {
            "chunk_id": "uncited",
            "document_version_id": "v1",
            "valid_from_epoch": 10,
            "valid_to_epoch": 20,
            "content_checksum": "old",
        }
    ]

    class Control:
        def _fetch_all(self, sql, params):
            return deepcopy(rows)

    class Release:
        def current_release(self, *args):
            return RetrievalRelease("t", "s", "g", 1, 1)

    class Visuals:
        def list_assets(self, version):
            return []

    snapshot = MySQLQASnapshot(Control(), Release(), Visuals(), "s")
    monkeypatch.setattr("ragkb.infrastructure.qa_snapshot.time.time", lambda: 11)
    original = snapshot("t", "s")
    rows[0]["content_checksum"] = "new-conflict"
    assert snapshot("t", "s") != original
    rows[0]["content_checksum"] = "old"
    monkeypatch.setattr("ragkb.infrastructure.qa_snapshot.time.time", lambda: 21)
    assert snapshot("t", "s") != original


def visual_fixture(tmp_path, replies):
    from ragkb.adapters.local_storage import LocalFileStorage
    from ragkb.adapters.visual_http import VisualAnalyzer
    from ragkb.infrastructure.visual_assets import VisualAssetStore
    from ragkb.infrastructure.visual_evidence import VisualEvidenceEnricher
    from test_visual_correctness import _evidence as visual_evidence
    from test_visual_pipeline import EXTRACTION, Transport, png, settings

    store = VisualAssetStore(LocalFileStorage(tmp_path))
    asset = store.save_image("v", png(), {"page": 12})
    asset.update(status="verified", section_path="Other subject", extraction=EXTRACTION)
    store.write_manifest("v", [asset])
    transport = Transport(*replies)
    owner = VisualEvidenceEnricher(store, VisualAnalyzer(settings(), transport), 3)
    return store, asset, transport, owner, visual_evidence(asset)


def test_no_crops_and_explicitly_unrelated_crop_use_zero_image_calls(tmp_path):
    store, asset, transport, owner, picture = visual_fixture(tmp_path, [])

    class Planner:
        def plan(self, question, assets, text):
            from ragkb.contracts.rag import EvidenceSelection

            return {a["id"] for a in assets}, EvidenceSelection((text[0].evidence_id,))

    owner.planner = Planner()
    plain = _evidence(evidence_id="E2", text="Warranty is three years.")
    assert len(owner("Warranty?", (plain,))) == 1
    session = owner.session("Warranty?")
    answer = session((picture, plain))
    assert len(answer) == 1 and answer[0].text == plain.text
    selection = session.preselected(answer)
    assert selection.source_ids == (answer[0].evidence_id,)
    assert (
        session.preselected((*answer, _evidence(evidence_id="E8", text="A new conflict"))) is None
    )
    assert session.preselected((replace(answer[0], text="Changed fact"),)) is None
    assert not transport.calls


def test_explicit_reference_cannot_be_excluded_by_text_planner(tmp_path):
    from test_visual_pipeline import PASS

    store, asset, transport, owner, picture = visual_fixture(
        tmp_path, [{"status": "supported", "text": "Power is 420 W.", "uncertainties": []}, PASS]
    )

    class Planner:
        def plan(self, *args):
            pytest.fail("Explicitly referenced crops must not be excluded")

    owner.planner = Planner()
    picture = replace(
        picture, locator={**picture.locator, "association_basis": "explicit_figure_reference"}
    )
    assert owner("Power?", (picture, _evidence(evidence_id="E2")))
    assert len(transport.calls) == 2


def test_exact_image_cache_keeps_independent_verification_and_invalidates_metadata(tmp_path):
    from test_visual_pipeline import PASS

    reply = {"status": "supported", "text": "Power is 420 W.", "uncertainties": []}
    store, asset, transport, owner, picture = visual_fixture(tmp_path, [reply, PASS, reply, PASS])
    first = owner("Power?", (picture,))
    session = owner.session("Power?")
    session.attempted = owner.max_images
    second = session((picture,))
    assert first == second and len(transport.calls) == 2
    asset["caption"] = "Revised figure context"
    store.write_manifest("v", [asset])
    assert owner("Power?", (picture,))
    assert len(transport.calls) == 4


def test_rejected_visual_does_not_drop_anchored_original_body(tmp_path):
    store, asset, transport, owner, picture = visual_fixture(
        tmp_path, [{"status": "not_relevant", "text": "", "uncertainties": []}]
    )
    picture = replace(
        picture,
        locator={
            **picture.locator,
            "nonvisual_source_spans": [
                {"chunk_id": "body", "text": "Warranty is three years.", "locator": {"page": 13}}
            ],
        },
    )
    result = owner("Warranty?", (picture,))
    assert result[0].text == "Warranty is three years."
    assert result[0].locator["page"] == 13
    assert not result[0].locator.get("visual_asset_ids")


def test_tampered_crop_cannot_be_excluded_using_stale_saved_extraction(tmp_path):
    from ragkb.domain.errors import QuestionAssessmentFailed

    store, asset, transport, owner, picture = visual_fixture(tmp_path, [])

    class Planner:
        def plan(self, *args):
            pytest.fail("Corrupt crop was considered for semantic exclusion")

    owner.planner = Planner()
    store.storage.path_for("artifacts", asset["storage_key"]).write_bytes(b"changed crop")
    with pytest.raises(QuestionAssessmentFailed, match="VISUAL_SOURCE_INVALID"):
        owner("Warranty?", (picture, _evidence(evidence_id="E2")))
    assert not transport.calls


def test_full_snapshot_refuses_missing_verified_crop(tmp_path):
    from ragkb.domain.retrieval import RetrievalRelease

    store, asset, _, _, _ = visual_fixture(tmp_path, [])

    class Control:
        def _fetch_all(self, *args):
            return [
                {
                    "chunk_id": "c",
                    "document_version_id": "v",
                    "valid_from_epoch": 0,
                    "valid_to_epoch": 0,
                }
            ]

    class Release:
        def current_release(self, *args):
            return RetrievalRelease("t", "s", "g", 1, 1)

    snapshot = MySQLQASnapshot(Control(), Release(), store, "s")
    assert snapshot("t", "s")
    store.storage.path_for("artifacts", asset["storage_key"]).write_bytes(b"changed crop")
    assert snapshot("t", "s") is None


def test_parallel_partition_balances_tail_without_dropping_conditions_or_exceeding_caps(tmp_path):
    settings, _ = _settings(tmp_path)
    _, sources = sample(17)
    required = condition_requirements(sources)
    serial = OpenAICompatibleClaimVerifier(
        settings.model_copy(update={"verifier_condition_parallelism": 1}),
        transport=ReviewTransport(),
    )
    parallel = OpenAICompatibleClaimVerifier(
        settings.model_copy(update={"verifier_condition_parallelism": 3}),
        transport=ReviewTransport(),
    )
    assert [len(b) for b in serial._condition_batches(required)] == [16, 1]
    balanced = parallel._condition_batches(required)
    assert [len(b) for b in balanced] == [9, 8]
    assert [r for b in balanced for r in b] == required
    assert all(
        sum(len(r["source_quote"]) for r in b) <= settings.verifier_condition_batch_characters
        for b in balanced
    )


def test_single_source_citations_preserve_facts_and_never_assign_multisource_rows():
    from ragkb.domain.rag import AtomicClaim
    from ragkb.domain.table_citations import attach_single_source_citations

    claims = (AtomicClaim("资料原文为硫喷妥纳", ("E4",)),)
    text = "资料原文为硫喷妥纳。\n\n| 名称 |\n| --- |\n| 硫喷妥纳 |\n"
    result = attach_single_source_citations(text, claims)
    assert "资料原文为硫喷妥纳。 [E4]" in result
    assert "| 硫喷妥纳 [E4] |" in result
    assert "硫喷妥钠" not in result
    assert attach_single_source_citations(result, claims) == result
    assert attach_single_source_citations(text, (*claims, AtomicClaim("other", ("E5",)))) == text
    assert attach_single_source_citations("```\ncode\n```", claims) == "```\ncode\n```"


def test_shared_visual_plan_uses_one_selection_call_and_validates_source_witness(tmp_path):
    import json

    from ragkb.adapters.visual_relevance import VisualRelevancePlanner
    from test_model_http_adapters import _MockTransport

    settings, _ = _settings(tmp_path)
    check = {
        "id": "1",
        "decision": "unrelated_scope",
        "reason": "Power crop, warranty question",
        "text_source_id": "E2",
    }
    value = {
        "source_ids": ["E2"],
        "coverage": "sufficient",
        "queries": [],
        "clarification": None,
        "visual_checks": [check],
    }
    transport = _MockTransport({"choices": [{"message": {"content": json.dumps(value)}}]})
    planner = VisualRelevancePlanner(settings, transport)
    excluded, selection = planner.plan("Warranty?", [{"id": "1"}], (_evidence(evidence_id="E2"),))
    assert excluded == {"1"} and selection.source_ids == ("E2",)
    assert len(transport.calls) == 1
    check["text_source_id"] = "E999"
    transport = _MockTransport({"choices": [{"message": {"content": json.dumps(value)}}]})
    with pytest.raises(InvalidProviderResponse):
        VisualRelevancePlanner(settings, transport).plan(
            "Warranty?", [{"id": "1"}], (_evidence(evidence_id="E2"),)
        )


def test_new_explicit_reference_reopens_a_previous_crop_exclusion(tmp_path):
    from ragkb.contracts.rag import EvidenceSelection
    from test_visual_pipeline import PASS

    _, _, transport, owner, picture = visual_fixture(
        tmp_path, [{"status": "supported", "text": "Power is 420 W.", "uncertainties": []}, PASS]
    )

    class Planner:
        def plan(self, question, assets, text):
            return {a["id"] for a in assets}, EvidenceSelection((text[0].evidence_id,))

    owner.planner = Planner()
    session = owner.session("Power?")
    plain = _evidence(evidence_id="E2")
    first = session((picture, plain))
    assert session.preselected(first) is not None and not transport.calls
    explicit = replace(
        picture, locator={**picture.locator, "association_basis": "explicit_figure_reference"}
    )
    result = session((explicit, plain))
    assert len(transport.calls) == 2
    assert session.preselected(result) is None


def test_required_crop_failure_cannot_reuse_earlier_text_coverage(tmp_path):
    from ragkb.contracts.rag import EvidenceSelection

    _, _, transport, owner, picture = visual_fixture(
        tmp_path, [{"status": "uncertain", "text": "", "uncertainties": ["Values unclear"]}]
    )

    class Planner:
        def plan(self, question, assets, text):
            return set(), EvidenceSelection((text[0].evidence_id,))

    owner.planner = Planner()
    session = owner.session("Power?")  # A figure can be needed without the word image.
    plain = _evidence(evidence_id="E2")
    result = session((picture, plain))
    assert len(transport.calls) == 1
    assert len(result) == 1 and result[0].text == plain.text
    assert session.preselected(result) is None


def test_snapshot_failure_at_cached_release_falls_back_without_releasing_old_answer(tmp_path):
    service, provider, _, _, _ = cached_service(tmp_path)
    assert service.ask("保修期多久？", "tenant", "user").verified
    count = [0]

    def snapshot(*args):
        count[0] += 1
        if count[0] > 1:
            raise OSError("snapshot temporarily unavailable")
        return "all-published-v1"

    service.result_reuse.snapshot = snapshot
    service.generator.fail = True
    result = service.ask("保修期多久？", "tenant", "user")
    assert not result.verified


@pytest.mark.skipif(
    os.environ.get("RAG_RUN_REDIS_ACCOUNT_TEST") != "1", reason="isolated Redis opt-in"
)
def test_live_redis_worker_capacity_obeys_tokens_slots_waiters_and_cooldown():
    from ragkb.config import load_env
    from ragkb.infrastructure.model_account import AccountLimiter

    settings = load_env().settings.model_copy(
        update={
            "redis_key_prefix": "qa-speed-test:" + uuid.uuid4().hex + ":",
            "model_account_group": "isolated-capacity",
            "model_account_max_concurrency": 3,
            "model_account_tokens_per_minute": 1000,
            "model_account_requests_per_minute": 20,
        }
    )
    limiter = AccountLimiter(settings)
    keys = limiter._keys("https://example.invalid", {})

    def capacity(tokens=200):
        return limiter.available_workers("https://example.invalid", {}, tokens)

    try:
        assert capacity() == 3
        assert capacity(600) == 1
        limiter.redis.zadd(keys[0], {"one": time.time() + 60, "two": time.time() + 60})
        assert capacity() == 1
        limiter.redis.delete(keys[0])
        limiter.redis.zadd(keys[1], {"past-call": time.time()})
        limiter.redis.hset(keys[2], "past-call", 700)
        assert capacity() == 1
        limiter.redis.delete(keys[1], keys[2])
        limiter.redis.zadd(keys[4], {"waiting": time.time()})
        assert capacity() == 1
        limiter.redis.delete(keys[4])
        limiter.redis.set(keys[3], time.time() + 60)
        assert capacity() == 1
    finally:
        limiter.redis.delete(*keys)
        limiter.redis.close()
