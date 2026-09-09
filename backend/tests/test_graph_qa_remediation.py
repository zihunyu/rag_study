"""End-to-end guards for source promotion, diagram branches and current-round facts."""

from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest
from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.application.qa import condition_repair_evidence
from ragkb.domain.answer_conditions import (
    condition_quotes,
    condition_requirements,
    validate_condition_checks,
)
from ragkb.domain.errors import InvalidProviderResponse
from ragkb.domain.rag import (
    AnswerStatus,
    AtomicClaim,
    ClaimVerdict,
    DraftAnswer,
    VerificationResult,
)
from ragkb.domain.visuals import VisualQueryOutcome
from ragkb.infrastructure.graph_evidence import approved_graph_evidence
from ragkb.infrastructure.visual_assets import VisualAssetStore
from ragkb.infrastructure.visual_evidence import VisualEvidenceEnricher
from test_trusted_qa import _evidence, _service
from test_visual_pipeline import EXTRACTION, png


def _draft(text, ids=("E1",)):
    return DraftAnswer(text, ids, (AtomicClaim(text, ids),), synthesized=True)


def test_missing_condition_in_review_pool_is_promoted_with_valid_citation_and_saved(tmp_path):
    original = _evidence(text="设备支持上门维修。")
    condition = replace(
        original,
        evidence_id="E2",
        chunk_id="condition",
        text="仅限保修期内且位于城区的设备。",
        source_role="conflict_context",
    )
    checks = (
        {
            "id": "K1",
            "evidence_id": "E2",
            "source_quote": condition.text,
            "status": "missing",
            "answer_quote": "",
            "reason": "地域和保修前提遗漏",
        },
    )

    class Generator:
        revision = "repair-fixture"
        calls = 0

        def generate(self, question, evidence):
            self.calls += 1
            assert self.calls == 1  # Exact source completion needs no model rewrite.
            assert [e.evidence_id for e in evidence] == ["E1"]
            return _draft("设备支持上门维修。[E1]")

    class Verifier:
        revision = "verified-condition-fixture"
        calls = 0

        def verify(self, question, draft, evidence):
            self.calls += 1
            if self.calls == 2:
                assert condition.text + " [E2]" in draft.text
                assert evidence[1].locator["conditions_to_preserve"] == [condition.text]
            return VerificationResult(
                tuple(
                    ClaimVerdict(c.text, c.evidence_ids, "SUPPORTED", "OK") for c in draft.claims
                ),
                self.revision,
                condition_checks=checks
                if self.calls == 1
                else ({**checks[0], "status": "covered", "answer_quote": draft.text},),
            )

    generator = Generator()
    service, repository, _ = _service(
        tmp_path, SyntheticEvidenceProvider((original, condition)), generator=generator
    )
    service.verifier = Verifier()
    result = service.ask("能上门维修吗", "tenant", "user")
    assert result.status == AnswerStatus.ANSWERED and result.verified
    assert generator.calls == 1 and [c.evidence_id for c in result.citations] == ["E1", "E2"]
    assert service.verifier.calls == 2
    saved = repository.get_package(result.rag_run_id)
    assert {e.evidence_id for e in saved.generation_evidence} == {"E1", "E2"}


def test_condition_repair_does_not_truncate_mandatory_source_or_bypass_authorization():
    source = _evidence(text="仅限城区。" + "完整上下文" * 100)
    provider = SyntheticEvidenceProvider((source,))
    package = provider.build_package("范围？", "tenant", "user")
    checks = ({"evidence_id": "E1", "source_quote": "仅限城区。", "status": "missing"},)
    with pytest.raises(InvalidProviderResponse, match="BUDGET_EXCEEDED"):
        condition_repair_evidence(package, checks, cited_ids=("E1",), max_tokens=40)
    with pytest.raises(InvalidProviderResponse, match="SOURCE_INVALID"):
        condition_repair_evidence(
            replace(package, evidence=(replace(source, authorized=False),)),
            checks,
            cited_ids=("E1",),
        )


def test_repair_keeps_previously_covered_constraints_alongside_new_omissions():
    general = _evidence(text="保修期和免费保修除外情形按保修条款执行。")
    exclusion = replace(
        general,
        evidence_id="E2",
        chunk_id="exclusion",
        text="进水和擅自拆机损坏不在免费保修范围内。",
        source_role="conflict_context",
    )
    package = SyntheticEvidenceProvider((general, exclusion)).build_package(
        "可免费维修吗？", "tenant", "user"
    )
    checks = (
        {"evidence_id": "E1", "source_quote": general.text, "status": "covered"},
        {"evidence_id": "E2", "source_quote": exclusion.text, "status": "missing"},
    )
    repaired = condition_repair_evidence(package, checks, cited_ids=("E1",))
    assert [e.locator["conditions_to_preserve"] for e in repaired] == [
        [general.text],
        [exclusion.text],
    ]
    assert [e.text for e in repaired] == [general.text, exclusion.text]


@pytest.mark.parametrize("text", ["检测成功：是→结束，否→更换设备", "检测成功 指向 更换设备；否"])
def test_bare_yes_no_flow_branches_are_inventory_conditions(text):
    assert condition_quotes(text) == [text]


@pytest.mark.parametrize(
    "source,answer",
    [
        ("上门维修仅限保修期内且城区。", "上门维修仅限保修期内。[E1]"),
        ("上门维修仅限保修期内。", "支持上门维修。[E1]"),
        ("进水损坏不包括在免费维修内。", "进水损坏包括在免费维修内。[E1]"),
    ],
)
def test_shared_service_words_cannot_hide_missing_qualifier_or_negation(source, answer):
    required = condition_requirements((_evidence(text=source),))
    checked = validate_condition_checks(
        [{"id": "K1", "status": "covered", "answer_quote": answer, "reason": "声称已保留"}],
        required,
        _draft(answer),
        "维修条件",
    )
    assert checked[0]["status"] == "missing"


def test_related_repair_restrictions_cannot_be_marked_not_applicable():
    evidence = _evidence(text="设备支持维修。仅限城区。")
    checked = validate_condition_checks(
        [{"id": "K1", "status": "not_applicable", "answer_quote": "", "reason": "答案未涉及"}],
        condition_requirements((evidence,)),
        _draft("可维修。[E1]"),
        "维修范围是什么",
    )
    assert checked[0]["status"] == "missing"


def test_supported_visual_excerpt_replaces_old_ocr_and_preserves_missing_topics(tmp_path):
    store = VisualAssetStore(LocalFileStorage(tmp_path))
    asset = store.save_image("v", png(), {"part": "image"})
    asset.update(status="verified")
    store.write_manifest("v", [asset])

    class Analyzer:
        revision = "fixture"

        def query(self, *args):
            return VisualQueryOutcome(
                "supported", "A 功率为 100 W。", unanswered_topics=("维修范围",)
            )

    session = VisualEvidenceEnricher(store, Analyzer(), 2).session("功率和维修范围")
    old = _evidence(
        document_version_id="v",
        text="A 功率为 100 W；维修无限制。",
        locator={"visual_asset_ids": [asset["id"]]},
    )
    result = session((old,))
    assert "无限制" not in result[0].text and "100 W" in result[0].text
    assert result[0].locator["visual_unanswered_topics"] == ["维修范围"]
    assert result[0].locator["visual_facts"][0]["fact_id"]
    assert session(result) == result


def test_budget_is_spent_on_relevant_picture_and_reserved_for_supplement(tmp_path):
    store = VisualAssetStore(LocalFileStorage(tmp_path))
    items = []
    for index, text in enumerate(["设备外观", "维修范围", "保修流程"]):
        asset = store.save_image("v", png(), {"part": str(index)})
        asset.update(status="verified")
        store.write_manifest("v", [asset])
        items.append(
            _evidence(
                evidence_id=f"E{index + 1}",
                chunk_id=str(index),
                document_version_id="v",
                text=text,
                locator={"visual_asset_ids": [asset["id"]]},
            )
        )

    class Analyzer:
        revision = "fixture"
        calls = []

        def query(self, data, question, prior):
            self.calls.append(prior)
            return VisualQueryOutcome("supported", prior)

    analyzer = Analyzer()
    session = VisualEvidenceEnricher(store, analyzer, 2).session("维修范围")
    initial = session(tuple(items[:2]), reserve_images=1)
    assert analyzer.calls == ["维修范围"] and len(session.deferred) == 1
    supplement = replace(items[2], text="维修范围及保修流程")
    final = session((*initial, *session.deferred, supplement))
    assert analyzer.calls == ["维修范围", "维修范围及保修流程"]
    assert len(final) == 2 and session.attempted == 2


def test_human_graph_facts_keep_group_identity_and_pending_gaps_without_model():
    extraction = copy.deepcopy(EXTRACTION)
    asset = {
        "id": "a" * 32,
        "status": "verified",
        "origin": "human_review",
        "extraction": extraction,
    }
    outcome = approved_graph_evidence(asset, "version", "Gateway 连接关系")
    assert outcome and outcome.facts and not outcome.truncated
    assert all(f["fact_id"].startswith("GF-") for f in outcome.facts)
    extraction["graphs"][0]["edges"][0]["review_status"] = "pending"
    pending = approved_graph_evidence(asset, "version", "Gateway 连接关系")
    assert pending and all(f["kind"] != "edge" for f in pending.facts)
    assert pending.unanswered_topics
    assert approved_graph_evidence({**asset, "origin": "model"}, "version", "Gateway") is None


@pytest.mark.parametrize("kind", ["diagram", "mixed"])
def test_question_on_excluded_relation_cannot_fall_back_to_rereading_original(kind):
    extraction = copy.deepcopy(EXTRACTION)
    extraction["kind"] = kind
    extraction["graphs"][0]["edges"][0]["review_status"] = "excluded"
    asset = {
        "id": "a" * 32,
        "status": "verified",
        "origin": "human_review",
        "extraction": extraction,
    }
    outcome = approved_graph_evidence(asset, "version", "故障怎么办")
    assert outcome is not None and outcome.status == "uncertain"
    assert not outcome.text and not outcome.facts


def test_explicit_branch_cannot_swap_destination_or_omit_negative_premise():
    source = "检测成功 指向 更换设备；判断：检测成功；分支：否"
    fact = {
        "fact_id": "GF-test",
        "text": source,
        "source": "检测成功",
        "target": "更换设备",
        "condition": "判断：检测成功；分支：否",
    }
    required = condition_requirements((_evidence(text=source, locator={"visual_facts": [fact]}),))
    assert required[0]["kind"] == "workflow_branch"
    for answer in ("检测成功则更换设备。[E1]", "检测失败则结束。[E1]"):
        checked = validate_condition_checks(
            [{"id": "K1", "status": "covered", "answer_quote": answer, "reason": "声称覆盖"}],
            required,
            _draft(answer),
            "检测失败怎么办",
        )
        assert checked[0]["status"] == "missing"


def test_partial_graph_coverage_is_verified_and_cannot_be_dismissed(tmp_path):
    from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
    from test_model_http_adapters import _settings
    from test_visual_binding_conditions import ConditionTransport

    limit = "本图覆盖限制：仅能确认已提供的部分内容；其余分支未确认"
    evidence = _evidence(
        text="Gateway 指向 Server\n" + limit,
        locator={
            "graph_query_complete": False,
            "graph_query_truncated": True,
            "visual_unanswered_topics": ["其余分支未确认"],
            "visual_coverage_quote": limit,
        },
    )
    required = condition_requirements((evidence,))
    assert any(r.get("kind") == "coverage_limit" for r in required)
    checks = [
        {"id": r["id"], "status": "not_applicable", "answer_quote": "", "reason": "答案没有写"}
        for r in required
    ]
    result = validate_condition_checks(
        checks, required, _draft("Gateway 连接 Server，是完整流程。[E1]"), "总结架构流程"
    )
    assert all(c["status"] == "missing" for c in result)
    settings, _ = _settings(tmp_path)
    transport = ConditionTransport()
    OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        "总结架构流程", _draft("Gateway 连接 Server。[E1]"), (evidence,)
    )
    sent = json.loads(transport.calls[-1]["payload"]["messages"][1]["content"])
    assert sent["conflict_evidence"][0]["visual_context"]["graph_query_complete"] is False
    assert (
        sent["claim_evidence_sources"][sent["claims"][0]["evidence"][0]["source_ref"]][
            "visual_context"
        ]["graph_query_truncated"]
        is True
    )


def test_model_output_cannot_award_human_status_or_certify_bounding_boxes():
    from ragkb.adapters.visual_http import VisualAnalyzer
    from ragkb.domain.visuals import VisualExtraction
    from test_visual_pipeline import Transport, settings

    raw = copy.deepcopy(EXTRACTION)
    node = raw["graphs"][0]["nodes"][0]
    node.update(review_status="confirmed", bbox=[0.1, 0.1, 0.2, 0.2], bbox_basis="human")
    parsed, _ = VisualAnalyzer(settings(), Transport(raw))._call(png(), "fixture", VisualExtraction)
    assert parsed.graphs[0].nodes[0].review_status == "inherited"
    assert parsed.graphs[0].nodes[0].bbox_basis == "unverified"


def test_overview_partial_graph_gaps_reach_overall_completeness(tmp_path):
    from types import SimpleNamespace

    from ragkb.infrastructure.overview import OverviewReader

    store = VisualAssetStore(LocalFileStorage(tmp_path))
    raw = copy.deepcopy(EXTRACTION)
    raw["graphs"][0]["edges"][0]["review_status"] = "pending"
    asset = store.save_image("v", png(), {"part": "image"})
    asset.update(status="verified", origin="human_review", extraction=raw)
    store.write_manifest("v", [asset])
    source = _evidence(
        document_version_id="v", locator={"section_path": "架构", "visual_asset_ids": [asset["id"]]}
    )
    chunk = SimpleNamespace(
        **{
            **source.__dict__,
            "retrieval_text": "Gateway 连接关系",
            "display_text": "Gateway 连接关系",
        }
    )

    class Repository:
        def list_documents_page(self, *args, **kwargs):
            return SimpleNamespace(
                items=[
                    {"document_id": source.document_id, "version_id": "v", "filename": "fixture.md"}
                ],
                next_key=None,
            )

        def list_chunks_page(self, *args, **kwargs):
            return SimpleNamespace(items=[{"chunk_id": source.chunk_id}], next_key=None)

    class Authorization:
        def authorize_chunks(self, *args, **kwargs):
            return {source.chunk_id: chunk}

    class Analyzer:
        revision = "fixture"

        def query(self, *args):
            pytest.fail("Approved partial structure must not re-read original pixels")

    config = SimpleNamespace(
        overview_max_chunks=100, overview_max_images=2, overview_evidence_tokens=8000
    )
    reader = OverviewReader(
        Repository(),
        Authorization(),
        store,
        config,
        None,
        VisualEvidenceEnricher(store, Analyzer(), 2),
    )
    evidence, report = reader.read(
        "总结架构", SimpleNamespace(space_ids=("space",), tenant_id="tenant")
    )
    assert evidence and not report["complete"]
    assert any("未确认或排除的关系" in gap for gap in report["gaps"])
    assert report["sections"][0]["state"] == "incomplete"
    assert all(e.locator["reading_coverage_complete"] is False for e in evidence)


def _graph_claim_source():
    first = {
        "fact_id": "GF-edge-1",
        "kind": "edge",
        "text": "Gateway 指向 Server。",
        "asset_id": "a",
        "document_version_id": "internal-version-id",
        "review_status": "confirmed",
    }
    second = {**first, "fact_id": "GF-edge-2", "text": "Server 指向 Database。"}
    return _evidence(
        text=first["text"] + "\n" + second["text"],
        locator={"visual_asset_ids": ["a"], "visual_facts": [first, second]},
    )


@pytest.mark.parametrize(
    "ids,error",
    [((), "REQUIRED"), (("GF-missing",), "INVALID"), (("GF-edge-1", "GF-edge-1"), "INVALID")],
)
def test_graph_claim_requires_exact_distinct_fact_ids_from_its_own_evidence(ids, error):
    from ragkb.domain.visual_claims import visual_claim_evidence

    with pytest.raises(ValueError, match=error):
        visual_claim_evidence(
            AtomicClaim("Gateway 指向 Server。", ("E1",), ids), (_graph_claim_source(),)
        )


def test_graph_fact_scope_and_selected_fact_text_are_enforced():
    from ragkb.domain.visual_claims import visual_claim_evidence

    source = _graph_claim_source()
    claim = AtomicClaim("Gateway 指向 Server。", ("E1",), ("GF-edge-1",))
    narrowed = visual_claim_evidence(claim, (source,))
    assert narrowed[0].text == "Gateway 指向 Server。"
    invalid = copy.deepcopy(source.locator)
    invalid["visual_facts"][0]["document_version_id"] = "withdrawn-version"
    with pytest.raises(ValueError, match="SCOPE_INVALID"):
        visual_claim_evidence(claim, (replace(source, locator=invalid),))
    unrelated = replace(source, evidence_id="E2", chunk_id="other-image")
    with pytest.raises(ValueError, match="INVALID"):
        visual_claim_evidence(
            AtomicClaim("Gateway 指向 Server。", ("E3",), ("GF-edge-1",)), (unrelated,)
        )


def test_only_used_fact_ids_are_saved_for_source_location_and_survive_answer_cache(tmp_path):
    from ragkb.adapters.rag_cache import RedisVerifiedAnswerCache
    from ragkb.application.qa import verified_answer_cache_key

    source = _graph_claim_source()
    claim = AtomicClaim("Gateway 指向 Server。", ("E1",), ("GF-edge-1",))

    class Generator:
        revision = "graph-claim-fixture"

        def generate(self, *args):
            return DraftAnswer(claim.text, ("E1",), (claim,))

    service, repository, _ = _service(
        tmp_path, SyntheticEvidenceProvider((source,)), generator=Generator()
    )
    result = service.ask("Gateway 连接谁", "tenant", "user")
    assert result.status == AnswerStatus.ANSWERED and result.verified
    assert result.citations[0].locator["used_visual_fact_ids"] == ["GF-edge-1"]
    assert repository.get_evidence(result.rag_run_id, "E1").locator["used_visual_fact_ids"] == [
        "GF-edge-1"
    ]
    package = repository.get_package(result.rag_run_id)
    assert verified_answer_cache_key(package) == verified_answer_cache_key(
        replace(package, evidence=(source,))
    )

    class Cache:
        value = None

        def set_json(self, namespace, key, value, ttl):
            self.value = value

        def get_json(self, *args):
            return self.value

    cache = RedisVerifiedAnswerCache(Cache(), ttl_seconds=30)
    draft = Generator().generate()
    cache.put(package, draft)
    assert cache.get(package).claims[0].visual_fact_ids == ("GF-edge-1",)


def test_semantic_graph_review_receives_only_declared_facts(tmp_path):
    from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
    from test_model_http_adapters import _settings
    from test_visual_binding_conditions import ConditionTransport

    source = _graph_claim_source()
    claim = AtomicClaim("Gateway 指向 Server。", ("E1",), ("GF-edge-1",))
    settings, _ = _settings(tmp_path)
    transport = ConditionTransport()
    OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        "Gateway 连接谁", DraftAnswer(claim.text, ("E1",), (claim,)), (source,)
    )
    sent = json.loads(transport.calls[-1]["payload"]["messages"][1]["content"])
    assert sent["claims"][0]["visual_fact_ids"] == ["GF-edge-1"]
    assert (
        "Database"
        not in sent["claim_evidence_sources"][sent["claims"][0]["evidence"][0]["source_ref"]][
            "text"
        ]
    )
    assert "Database" in sent["conflict_evidence"][0]["text"]


def test_human_approved_footnotes_are_preserved_as_separate_facts_but_partial_text_is_not():
    raw = copy.deepcopy(EXTRACTION)
    raw["body_text"] = "仅限城区且保修期内。"
    asset = {"id": "a" * 32, "status": "verified", "origin": "human_review", "extraction": raw}
    full = approved_graph_evidence(asset, "v", "总结流程")
    assert "仅限城区且保修期内" in full.text
    assert any(f["kind"] == "note" and not f["bbox"] for f in full.facts)
    raw["graphs"][0]["edges"][0]["review_status"] = "excluded"
    partial = approved_graph_evidence(asset, "v", "总结流程")
    assert "仅限城区且保修期内" not in partial.text


def test_generator_requires_and_preserves_visual_fact_ids(tmp_path):
    from test_generation_outcomes import generator_for

    payload = {
        "status": "answered",
        "answer": "Gateway 指向 Server。",
        "citation_ids": ["E1"],
        "claims": [{"text": "Gateway 指向 Server。", "evidence_ids": ["E1"]}],
    }
    generator, _ = generator_for(tmp_path, payload)
    with pytest.raises(InvalidProviderResponse, match="CLAIM_VISUAL_FACT_REQUIRED"):
        generator.generate("Gateway 连接谁", (_graph_claim_source(),))
    payload["claims"][0]["visual_fact_ids"] = ["GF-edge-1"]
    generator, _ = generator_for(tmp_path, payload)
    answer = generator.generate("Gateway 连接谁", (_graph_claim_source(),))
    assert answer.claims[0].visual_fact_ids == ("GF-edge-1",)


def test_note_only_claim_is_generated_verified_and_locates_legal_source_without_fake_box(tmp_path):
    from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
    from ragkb.domain.rag import AskResult, Citation
    from ragkb.domain.visual_claims import visual_claim_evidence
    from ragkb.infrastructure.graph_citations import cited_graph_targets
    from test_generation_outcomes import generator_for
    from test_model_http_adapters import _settings
    from test_visual_binding_conditions import ConditionTransport

    raw = copy.deepcopy(EXTRACTION)
    raw["body_text"] = "仅限城区且保修期内。"
    asset = {"id": "a" * 32, "status": "verified", "origin": "human_review", "extraction": raw}
    outcome = approved_graph_evidence(asset, "v", "总结流程")
    note = next(f for f in outcome.facts if f["kind"] == "note")
    source = _evidence(
        document_version_id="v",
        text=outcome.text,
        locator={
            "visual_asset_ids": [asset["id"]],
            "visual_facts": list(outcome.facts),
            "used_visual_fact_ids": [note["fact_id"]],
        },
    )
    payload = {
        "status": "answered",
        "answer": raw["body_text"],
        "citation_ids": ["E1"],
        "claims": [
            {"text": raw["body_text"], "evidence_ids": ["E1"], "visual_fact_ids": [note["fact_id"]]}
        ],
    }
    generator, _ = generator_for(tmp_path, payload)
    draft = generator.generate("流程有哪些限制", (source,))
    narrowed = visual_claim_evidence(draft.claims[0], (source,))
    assert narrowed[0].text == raw["body_text"] and "Gateway" not in narrowed[0].text
    settings, _ = _settings(tmp_path)

    def covered(response):
        for check in response["condition_checks"]:
            check.update(
                status="covered", answer_quote=draft.text, reason="城区和保修期两个条件均保留"
            )

    verifier = OpenAICompatibleClaimVerifier(settings, transport=ConditionTransport(covered))
    assert verifier.verify("流程有哪些限制", draft, (source,)).supported
    result = AskResult(
        rag_run_id="run",
        status=AnswerStatus.ANSWERED,
        answer=draft.text,
        citations=(Citation("E1", "/source", source.locator),),
        evidence=(source,),
        warnings=(),
        verified=True,
        real_acceptance=False,
    )
    assert cited_graph_targets(source, asset, result) == []


def test_flow_from_named_entry_includes_downstream_decision_and_return_edge():
    graph = {
        "direction": "TB",
        "groups": [],
        "uncertainties": [],
        "nodes": [
            {"id": identity, "label": label, "group": None, "shape": shape}
            for identity, label, shape in [
                ("entry", "入口", "rectangle"),
                ("check", "检测成功", "diamond"),
                ("repair", "更换设备", "rectangle"),
                ("finish", "结束", "rectangle"),
            ]
        ],
        "edges": [
            {
                "source": source,
                "target": target,
                "label": label,
                "direction": "forward",
                "style": "solid",
            }
            for source, target, label in [
                ("entry", "check", ""),
                ("check", "finish", "是"),
                ("check", "repair", "否"),
                ("repair", "check", "重新检测"),
            ]
        ],
    }
    raw = {
        "kind": "diagram",
        "title": "",
        "transcription": "",
        "description": "",
        "tables": [],
        "graphs": [graph],
        "uncertainties": [],
    }
    asset = {"id": "a" * 32, "status": "verified", "origin": "human_review", "extraction": raw}
    result = approved_graph_evidence(asset, "v", "从入口开始介绍整个流程")
    assert not result.truncated and not result.unanswered_topics
    edges = [f for f in result.facts if f["kind"] == "edge"]
    assert len(edges) == 4 and any("分支：否" in f["condition"] for f in edges)
    assert any(f["source_id"] == "repair" and f["target_id"] == "check" for f in edges)


@pytest.mark.parametrize(
    "bbox", [[0, 0, 0, 0], [100, 200, 300, 400], [-0.1, 0.2, 0.3, 0.4], "unknown"]
)
def test_optional_unverified_model_coordinates_are_dropped_without_changing_structure(bbox):
    from ragkb.adapters.visual_http import VisualAnalyzer
    from ragkb.domain.visuals import VisualExtraction
    from test_visual_pipeline import Transport, settings

    raw = copy.deepcopy(EXTRACTION)
    raw["graphs"][0]["nodes"][0].update(bbox=bbox, bbox_basis="human", review_status="confirmed")
    parsed, receipt = VisualAnalyzer(settings(), Transport(raw))._call(
        png(), "fixture", VisualExtraction
    )
    assert parsed.graphs[0].nodes[0].bbox is None
    assert parsed.graphs[0].nodes[0].bbox_basis == "unverified"
    assert parsed.graphs[0].edges[0].source == raw["graphs"][0]["edges"][0]["source"]
    assert receipt["coordinate_normalizations"][0]["path"] == ["graphs", 0, "nodes", 0, "bbox"]


def test_schema_audit_records_fixed_paths_and_error_types_without_raw_values():
    from ragkb.adapters.visual_http import VisualAnalyzer
    from test_visual_pipeline import Transport, settings

    raw = copy.deepcopy(EXTRACTION)
    raw["graphs"][0]["nodes"][0]["shape"] = "private-fixture-value"
    raw["private-fixture-key"] = "do-not-echo-input"
    analyzer = VisualAnalyzer(
        settings().model_copy(update={"ocr_max_repair_attempts": 0}), Transport(raw)
    )
    result = analyzer.analyze(png())
    assert result["issues"] == ["OCR_STRUCTURE_INVALID"]
    audit = json.dumps(result["audit"], ensure_ascii=False)
    assert "literal_error" in audit and "shape" in audit and "unexpected_field" in audit
    assert "private-fixture" not in audit and "do-not-echo-input" not in audit


def test_complete_mixed_human_graph_preserves_table_and_notes():
    from test_visual_binding_conditions import table_extraction

    raw = copy.deepcopy(EXTRACTION)
    raw["kind"], raw["body_text"] = "mixed", "仅限城区且保修期内。"
    raw["tables"] = table_extraction([["产品", "功率"], ["A", "100 W"]]).model_dump()["tables"]
    asset = {"id": "a" * 32, "status": "verified", "origin": "human_review", "extraction": raw}
    outcome = approved_graph_evidence(asset, "v", "总结流程和功率")
    assert outcome.status == "supported"
    assert "仅限城区" in outcome.text and "100 W" in outcome.text
    assert {f["kind"] for f in outcome.facts}.issuperset({"edge", "note", "table"})


def test_faithful_branch_answer_survives_citation_format_drift_and_clear_pronoun():
    yes = "是否解决? 指向 完成；连线标注：是；判断：是否解决?；分支：是"
    no = "是否解决? 指向 上门处理；连线标注：否；判断：是否解决?；分支：否"
    notes = "上门处理仅限保修期内且位于城区的设备。进水损坏不在免费维修范围内。"
    facts = [
        {
            "fact_id": "GF-" + label,
            "kind": "edge",
            "text": text,
            "source": "是否解决?",
            "target": target,
            "condition": "判断：是否解决?；分支：" + label,
        }
        for label, target, text in [("是", "完成", yes), ("否", "上门处理", no)]
    ]
    source = _evidence(text=notes + "\n" + yes + "\n" + no, locator={"visual_facts": facts})
    answer = (
        "诊断后判断“是否解决？”。如果结果为“是”，流程进入**完成**；"
        "如果结果为“否”，则转入**上门处理**。[E1]\n\n"
        "上门处理仅限保修期内且位于城区的设备；进水损坏不在免费维修范围内。[E1]"
    )
    required = condition_requirements((source,))
    assert len(required) == 4
    checks = []
    for row in required:
        quote = (
            "仅限保修期内且位于城区的设备"
            if "仅限" in row["source_quote"]
            else "进水损坏不在免费维修范围内"
        )
        if row.get("target") == "完成":
            quote = "如果结果为“是”，流程进入**完成**。[E1]"
        elif row.get("target") == "上门处理":
            quote = "如果结果为“否”，则转入**上门处理**。[E1]"
        checks.append(
            {
                "id": row["id"],
                "status": "covered",
                "answer_quote": quote,
                "reason": "已独立核对所有条件和分支",
            }
        )
    result = validate_condition_checks(checks, required, _draft(answer), "总结完整流程")
    assert all(c["status"] == "covered" and c["answer_quote"] in answer for c in result)
    checks[2]["answer_quote"] = "如果结果为“是”，流程进入**上门处理**。[E1]"
    with pytest.raises(ValueError, match="WITNESS_INVALID"):
        validate_condition_checks(checks, required, _draft(answer), "总结完整流程")


def _linked_figure_provider(tmp_path):
    from types import SimpleNamespace

    from ragkb.domain.retrieval import SearchHit, SearchResult, SearchSource
    from ragkb.infrastructure.overview import OverviewReader
    from test_rag_general_repair import provider

    store = VisualAssetStore(LocalFileStorage(tmp_path))
    assets, sources = [], []
    for index, (title, origin, target) in enumerate(
        [("A架构图", "Gateway", "NACOS"), ("B架构图", "NACOS", "Database")], 1
    ):
        raw = copy.deepcopy(EXTRACTION)
        raw.update(title=title, body_text="")
        raw["graphs"] = [
            {
                "direction": "TB",
                "groups": [],
                "uncertainties": [],
                "nodes": [
                    {"id": label.lower(), "label": label, "group": None, "shape": "rectangle"}
                    for label in [origin, target]
                ],
                "edges": [
                    {
                        "source": origin.lower(),
                        "target": target.lower(),
                        "label": "",
                        "direction": "forward",
                        "style": "solid",
                    }
                ],
            }
        ]
        asset = store.save_image("v", png(), {"part": f"image{index}"})
        asset.update(
            status="verified",
            origin="human_review",
            extraction=raw,
            caption=f"图{index}",
            section_path="系统架构拓扑图",
        )
        store.write_manifest("v", [asset])
        assets.append(asset)
        text = title + ("；后续内容参见图2。" if index == 1 else "")
        sources.append(
            SearchSource(
                f"chunk{index}",
                "doc",
                "v",
                text,
                text,
                {"visual_asset_ids": [asset["id"]], "section_path": asset["section_path"]},
                0,
                0,
                1,
                True,
            )
        )

    class Repository:
        def list_chunks_page(self, version, **kwargs):
            assert version == "v"
            return SimpleNamespace(
                items=[{"chunk_id": s.chunk_id, "locator": s.locator} for s in sources],
                next_key=None,
            )

    class Authorization:
        def authorize_chunks(self, ids, context):
            assert context.space_ids == ("space",)
            return {s.chunk_id: s for s in sources if s.chunk_id in ids}

    class Search:
        revision = "linked-figure-fixture"

        def search(self, *args, **kwargs):
            source = sources[0]
            return SearchResult(
                (
                    SearchHit(
                        source.chunk_id,
                        "doc",
                        "v",
                        source.display_text,
                        source.locator,
                        1,
                        1,
                        ("dense",),
                        permission_revision=1,
                        display_text=source.display_text,
                        retrieval_text=source.retrieval_text,
                    ),
                ),
                1,
            )

        def expand_parents(self, *args):
            return ()

    class Analyzer:
        revision = "approved-structure-fixture"

        def query(self, *args):
            pytest.fail("Approved structure must not trigger external model calls")

    pipeline = provider(Search(), None)
    pipeline.visual_enricher = VisualEvidenceEnricher(store, Analyzer(), 2)
    pipeline.overview_reader = OverviewReader(
        Repository(), Authorization(), store, SimpleNamespace(overview_max_chunks=100), None
    )
    return pipeline, assets


def test_explicit_figure_reference_can_answer_local_facts_without_inventing_cross_figure_path(
    tmp_path,
):
    from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
    from test_generation_outcomes import generator_for
    from test_model_http_adapters import _settings
    from test_visual_binding_conditions import ConditionTransport

    pipeline, assets = _linked_figure_provider(tmp_path)
    question = "图1和图2能否证明 Gateway 到 Database 的完整路径？"
    package = pipeline.build_package(question, "tenant", "user")
    first, second = package.generation_evidence
    assert second.locator["association_basis"] == "explicit_figure_reference"
    assert first.locator["visual_associations"][0]["target_asset_id"] == assets[1]["id"]
    assert all(
        not link["node_mapping_confirmed"]
        for item in package.evidence
        for link in item.locator["visual_associations"]
    )
    assert "参见图2" not in first.text  # The raw OCR reference is not an answer fact.
    assert all(e.locator["cross_graph_path_complete"] is False for e in package.evidence)
    fact_sets = [{f["fact_id"] for f in e.locator["visual_facts"]} for e in package.evidence]
    assert fact_sets[0].isdisjoint(fact_sets[1])  # Even identical NACOS node IDs are isolated.
    assert not any(
        f.get("source") == "Gateway" and f.get("target") == "Database"
        for e in package.evidence
        for f in e.locator["visual_facts"]
    )
    note = first.locator["visual_coverage_quote"]
    answer = "A架构图中 Gateway 指向 NACOS。[E1]\n\nB架构图中 NACOS 指向 Database。[E2]\n\n" + note
    claims = [
        {
            "text": sentence,
            "evidence_ids": [item.evidence_id],
            "visual_fact_ids": [
                f["fact_id"]
                for f in item.locator["visual_facts"]
                if f["kind"] in {"edge", "source_context"}
            ],
        }
        for item, sentence in [
            (first, "A架构图中 Gateway 指向 NACOS。"),
            (second, "B架构图中 NACOS 指向 Database。"),
        ]
    ]
    generator, transport = generator_for(
        tmp_path,
        {
            "status": "answered",
            "format": "synthesized_markdown",
            "answer": answer,
            "citation_ids": ["E1", "E2"],
            "claims": claims,
        },
    )

    def covered(response):
        for check in response["condition_checks"]:
            check.update(status="covered", answer_quote=note, reason="明确声明跨图映射未核验")

    settings, _ = _settings(tmp_path)
    verifier_transport = ConditionTransport(covered)
    service, repository, _ = _service(tmp_path, pipeline, generator=generator)
    service.verifier = OpenAICompatibleClaimVerifier(settings, transport=verifier_transport)
    result = service.ask(question, "tenant", "user")
    assert result.verified and result.status is AnswerStatus.ANSWERED
    assert len(result.citations) == 2 and "不能仅凭同名" in result.answer
    for item, citation in zip(package.evidence, result.citations, strict=True):
        assert set(citation.locator["used_visual_fact_ids"]) <= {
            f["fact_id"] for f in item.locator["visual_facts"]
        }
        assert repository.get_evidence(result.rag_run_id, citation.evidence_id).authorized
    generated = transport.calls[0]["payload"]["messages"][1]["content"]
    assert "visual_associations" in generated and "visual_source_context" in generated
    verified = json.loads(verifier_transport.calls[0]["payload"]["messages"][1]["content"])
    assert all(
        e["visual_context"]["cross_graph_path_complete"] is False
        for e in verified["conflict_evidence"]
    )
    required = condition_requirements(package.evidence)
    missing = validate_condition_checks(
        [
            {"id": r["id"], "status": "not_applicable", "answer_quote": "", "reason": "省略说明"}
            for r in required
        ],
        required,
        _draft("完整路径为 Gateway→NACOS→Database。[E1][E2]", ("E1", "E2")),
        question,
    )
    assert missing and all(c["status"] == "missing" for c in missing)


def test_reviewed_figure_title_preserves_scope_without_false_cross_graph_gap(tmp_path):
    from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
    from test_model_http_adapters import _settings
    from test_visual_binding_conditions import ConditionTransport

    pipeline, assets = _linked_figure_provider(tmp_path)
    question = "A架构图中 Gateway 到 NACOS 的完整流程是什么"
    package = pipeline.build_package(question, "tenant", "user")
    first, second = package.generation_evidence
    assert "图片标题：A架构图" in first.text and "图片标题：B架构图" in second.text
    assert all("cross_graph_path_complete" not in e.locator for e in package.evidence)
    assert first.locator["visual_source_context"][assets[0]["id"]]["reviewed_title"] == "A架构图"
    claim = AtomicClaim(
        "A架构图中 Gateway 指向 NACOS。",
        ("E1",),
        tuple(
            f["fact_id"]
            for f in first.locator["visual_facts"]
            if f["kind"] in {"edge", "source_context"}
        ),
    )
    settings, _ = _settings(tmp_path)
    transport = ConditionTransport()
    verified = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        question, DraftAnswer(claim.text, ("E1",), (claim,)), package.evidence
    )
    assert verified.supported
    sent = json.loads(transport.calls[-1]["payload"]["messages"][1]["content"])
    selected = sent["claim_evidence_sources"][sent["claims"][0]["evidence"][0]["source_ref"]]
    assert "B架构图" not in selected["text"] and "Database" not in selected["text"]
    assert (
        selected["visual_context"]["visual_source_context"][assets[0]["id"]]["section_path"]
        == "系统架构拓扑图"
    )
