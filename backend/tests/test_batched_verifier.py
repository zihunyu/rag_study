from __future__ import annotations

import json
from dataclasses import replace

import pytest
from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.application.deadlines import request_deadline
from ragkb.domain.answer_conditions import condition_requirements
from ragkb.domain.errors import InvalidProviderResponse, ProviderTimeout
from ragkb.domain.rag import AtomicClaim, DraftAnswer
from test_model_http_adapters import _settings
from test_trusted_qa import _evidence, _service


def sample(count=93):
    sources = (_evidence(text="设备保修期为三年。"),) + tuple(
        _evidence(
            evidence_id=f"E{i + 1}",
            chunk_id=f"c{i}",
            document_id=f"d{i}",
            document_version_id=f"v{i}",
            text=f"产品 R{i} 维修仅限本市。",
            source_role="conflict_context",
        )
        for i in range(1, count + 1)
    )
    draft = DraftAnswer(
        "设备保修期为三年。[E1]",
        ("E1",),
        (AtomicClaim("设备保修期为三年。", ("E1",)),),
        synthesized=True,
    )
    return draft, sources


class ReviewTransport:
    real_network = False

    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform

    def post_json(self, url, **kwargs):
        data = json.loads(kwargs["payload"]["messages"][-1]["content"])
        self.calls.append(data)
        response = {
            "condition_checks": [
                {
                    "id": r["id"],
                    "applicable": False,
                    "status": "not_applicable",
                    "answer_quote": "",
                    "reason": "Question asks warranty duration, not repair region.",
                }
                for r in data["condition_requirements"]
            ]
        }
        if "conflict_evidence" in data:
            response.update(
                verdicts=[
                    {"claim_id": c["claim_id"], "verdict": "SUPPORTED", "reason_code": "OK"}
                    for c in data["claims"]
                ],
                answer_check={"covered": True, "citations_valid": True, "reason_code": "OK"},
                conflict_check={"checked": True, "conflicting_evidence_ids": []},
            )
        if self.transform:
            self.transform(data, response, kwargs)
        return {"choices": [{"message": {"content": json.dumps(response)}}]}


@pytest.mark.parametrize("count", [2, 93])
def test_single_status_protocol_preserves_missing_verdict_in_both_paths(tmp_path, count):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(count)

    def single_status(data, response, kwargs):
        for check in response["condition_checks"]:
            check.pop("applicable")
            if check["id"] == "K1":
                check.update(status="missing", reason="The applicable prerequisite is absent.")

    transport = ReviewTransport(single_status)
    result = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        "完整适用条件？", draft, sources
    )
    assert not result.supported
    assert len(result.condition_checks) == count
    assert result.condition_checks[0]["status"] == "missing"
    assert all(not c.get("protocol_repair") for c in transport.calls)


@pytest.mark.parametrize("failure", ["claim", "citation", "surface", "none"])
def test_combined_condition_repair_retains_prior_fact_and_surface_verdicts(tmp_path, failure):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(3)

    def partial(data, response, kwargs):
        if "conflict_evidence" in data:
            response["condition_checks"][1].update(
                applicable=True, status="covered", answer_quote="invented witness"
            )
            if failure == "claim":
                response["verdicts"][0]["verdict"] = "CONTRADICTED"
            if failure == "citation":
                response["answer_check"]["citations_valid"] = False
            if failure == "surface":
                response["answer_check"]["covered"] = False
        else:
            # Extra fields from a condition reviewer cannot override the first receipt.
            response["answer_check"] = {"covered": True, "citations_valid": True}
            response["verdicts"] = [{"verdict": "SUPPORTED"}]

    transport = ReviewTransport(partial)
    result = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        "保修期多久？", draft, sources
    )
    assert result.supported is (failure == "none")
    assert result.conflict_checked and len(result.condition_checks) == 3
    assert len(transport.calls) == 2
    initial, repair = transport.calls
    assert len(initial["conflict_evidence"]) == len(sources)
    assert "conflict_evidence" not in repair
    assert repair["answer"] == initial["answer"]
    assert [r["id"] for r in repair["condition_requirements"]] == ["K2"]


def test_combined_condition_repair_failure_is_not_followed_by_full_retry(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(3)

    def invalid(data, response, kwargs):
        for check in response["condition_checks"]:
            if check["id"] == "K2":
                check.update(applicable=True, status="covered", answer_quote="invented witness")

    transport = ReviewTransport(invalid)
    with pytest.raises(InvalidProviderResponse) as caught:
        OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
            "保修期多久？", draft, sources
        )
    assert len(transport.calls) == 2
    assert caught.value.diagnostic["condition_only_repair_attempted"]


def test_condition_repair_cannot_clear_a_confirmed_full_pool_conflict(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(2)
    sources = (sources[0], replace(sources[1], text="设备保修期为一年。"), sources[2])

    def conflict(data, response, kwargs):
        if "conflict_evidence" in data:
            response["conflict_check"].update(
                conflicting_evidence_ids=["E1", "E2"],
                pairs=[
                    {
                        "left_id": "E1",
                        "left_quote": sources[0].text,
                        "right_id": "E2",
                        "right_quote": sources[1].text,
                        "reason": "Same device and scope have incompatible warranty periods.",
                    }
                ],
            )
            response["condition_checks"][0].update(
                applicable=True, status="covered", answer_quote="invented witness"
            )
        else:
            response["conflict_check"] = {"checked": True, "conflicting_evidence_ids": []}

    transport = ReviewTransport(conflict)
    result = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        "保修期多久？", draft, sources
    )
    assert not result.supported and result.conflicting_evidence_ids == ("E1", "E2")
    assert result.conflict_checked and len(transport.calls) == 2


@pytest.mark.parametrize("count", [60, 86, 93])
def test_large_pool_reviews_every_condition_and_all_conflict_sources(tmp_path, count):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(count)
    transport = ReviewTransport()
    result = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        "保修期多久？", draft, sources
    )
    assert result.supported
    assert [c["id"] for c in result.condition_checks] == [f"K{i}" for i in range(1, count + 1)]
    assert transport.calls[0]["condition_requirements"] == []
    assert [e["evidence_id"] for e in transport.calls[0]["conflict_evidence"]] == [
        e.evidence_id for e in sources
    ]
    assert all("conflict_evidence" not in c for c in transport.calls[1:])
    assert all(0 < len(c["condition_requirements"]) <= 16 for c in transport.calls[1:])
    assert [r for c in transport.calls[1:] for r in c["condition_requirements"]] == [
        {**r, "cited_in_answer": False} for r in condition_requirements(sources)
    ]


def test_many_claims_plus_one_condition_batch_are_checked_separately(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(7)
    draft = replace(draft, claims=draft.claims * 15)
    transport = ReviewTransport()
    result = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        "保修期多久？", draft, sources
    )
    assert result.supported and len(result.verdicts) == 15
    assert len(result.condition_checks) == 7
    assert len(transport.calls) == 2
    assert transport.calls[0]["condition_requirements"] == []
    assert len(transport.calls[0]["conflict_evidence"]) == len(sources)
    assert len(transport.calls[1]["condition_requirements"]) == 7
    assert "conflict_evidence" not in transport.calls[1]


def test_timed_out_condition_batch_splits_once_without_losing_full_conflict_review(tmp_path):
    settings, _ = _settings(tmp_path)
    settings = settings.model_copy(update={"verifier_condition_batch_size": 8})
    draft, sources = sample(10)
    failed = False

    def timeout_first_batch(data, response, kwargs):
        nonlocal failed
        if "conflict_evidence" not in data and not failed:
            failed = True
            raise ProviderTimeout("MODEL_PROVIDER_DEADLINE_EXCEEDED")

    transport = ReviewTransport(timeout_first_batch)
    result = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        "保修期多久？", draft, sources
    )
    assert result.supported
    assert [c["id"] for c in result.condition_checks] == [f"K{i}" for i in range(1, 11)]
    assert len(transport.calls[0]["conflict_evidence"]) == len(sources)
    assert len(transport.calls) == 5  # base, failed batch, two halves, other batch
    assert len(transport.calls[2]["condition_requirements"]) <= 3
    assert len(transport.calls[3]["condition_requirements"]) <= 3


@pytest.mark.parametrize("code", ["MODEL_ACCOUNT_QUOTA_WAIT_TIMEOUT", "REQUEST_DEADLINE_EXCEEDED"])
def test_condition_batch_does_not_retry_queue_or_total_deadline_expiry(tmp_path, code):
    settings, _ = _settings(tmp_path)
    settings = settings.model_copy(update={"verifier_condition_batch_size": 8})
    draft, sources = sample(10)

    def unavailable(data, response, kwargs):
        if "conflict_evidence" not in data:
            raise ProviderTimeout(code)

    transport = ReviewTransport(unavailable)
    with pytest.raises(ProviderTimeout):
        OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
            "保修期多久？", draft, sources
        )
    assert len(transport.calls) == 2


def test_failed_half_does_not_recurse_or_release_partial_condition_results(tmp_path):
    settings, _ = _settings(tmp_path)
    settings = settings.model_copy(update={"verifier_condition_batch_size": 8})
    draft, sources = sample(10)

    def unavailable(data, response, kwargs):
        if "conflict_evidence" not in data:
            raise ProviderTimeout("MODEL_PROVIDER_DEADLINE_EXCEEDED")

    transport = ReviewTransport(unavailable)
    with pytest.raises(ProviderTimeout):
        OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
            "保修期多久？", draft, sources
        )
    assert len(transport.calls) == 3  # failed original + failed half, no recursion


def test_middle_batch_missing_condition_blocks_answer(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample()

    def missing(data, response, kwargs):
        for check in response["condition_checks"]:
            if check["id"] == "K37":
                check.update(
                    applicable=True, status="missing", reason="Relevant condition omitted."
                )

    result = OpenAICompatibleClaimVerifier(settings, transport=ReviewTransport(missing)).verify(
        "完整维修政策有哪些？", draft, sources
    )
    assert not result.supported
    assert len(result.condition_checks) == 93
    assert result.condition_checks[36]["status"] == "missing"
    assert result.verdicts[-1].reason_code == "ANSWER_KEY_CONDITION_MISSING"


def test_shared_long_source_does_not_duplicate_text_or_merge_claims(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(0)
    body = "设备保修期为三年。" * 1000
    sources = (replace(sources[0], text=body),)
    draft = replace(draft, claims=draft.claims * 11)
    transport = ReviewTransport()
    result = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        "保修期多久？", draft, sources
    )
    sent = transport.calls[0]
    assert result.supported and len(result.verdicts) == 11
    assert len(sent["claim_evidence_sources"]) == 1
    assert sent["claim_evidence_sources"]["S1"]["text"] == body
    assert all(c["evidence"] == [{"evidence_id": "E1", "source_ref": "S1"}] for c in sent["claims"])
    assert sent["conflict_evidence"][0]["text"] == body


def test_later_batch_covered_rule_requires_the_actual_cited_paragraph(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample()
    rule = sources[37]
    draft = replace(
        draft,
        text=draft.text + "\n\n" + rule.text + "[E38]",
        citation_ids=("E1", "E38"),
        claims=draft.claims + (AtomicClaim(rule.text, ("E38",)),),
    )

    def covered(data, response, kwargs):
        for check in response["condition_checks"]:
            if check["id"] == "K37":
                check.update(applicable=True, status="covered", answer_span_id="A2")

    result = OpenAICompatibleClaimVerifier(settings, transport=ReviewTransport(covered)).verify(
        "保修期限与 R37 维修地域限制？", draft, sources
    )
    assert result.supported
    assert result.condition_checks[36]["answer_quote"] == rule.text + "[E38]"


def test_verdict_count_repair_keeps_full_conflict_pool(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample()

    def wrong_count(data, response, kwargs):
        if "conflict_evidence" in data and not data.get("protocol_repair"):
            response["verdicts"] = []

    transport = ReviewTransport(wrong_count)
    assert (
        OpenAICompatibleClaimVerifier(settings, transport=transport)
        .verify("保修期多久？", draft, sources)
        .supported
    )
    assert len(transport.calls) == 8
    assert transport.calls[1]["protocol_repair"]["reason"] == "verdict_count_mismatch"
    assert transport.calls[0]["conflict_evidence"] == transport.calls[1]["conflict_evidence"]


def test_late_batch_invalid_witness_is_not_published(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample()

    def invalid(data, response, kwargs):
        for check in response["condition_checks"]:
            if check["id"] == "K93":
                check.update(applicable=True, status="covered", answer_quote="Invented answer text")

    transport = ReviewTransport(invalid)
    model = OpenAICompatibleClaimVerifier(settings, transport=transport)
    with pytest.raises(InvalidProviderResponse) as caught:
        model.verify("保修期多久？", draft, sources)
    assert caught.value.diagnostic["condition_id"] == "K93"
    assert caught.value.diagnostic["batch_number"] == 6
    assert caught.value.diagnostic["completed_batches"] == 5
    assert len(transport.calls) == 8  # base, six batches, one repair of batch six


def test_batch_repair_only_rechecks_invalid_rows_and_preserves_missing_rules(tmp_path):
    from ragkb.application.qa_performance import performance_report, performance_scope

    settings, _ = _settings(tmp_path)
    draft, sources = sample()

    def partial(data, response, kwargs):
        for check in response["condition_checks"]:
            if check["id"] == "K1":
                check.update(applicable=True, status="missing", reason="Applicable rule is absent.")
            if check["id"] == "K3" and not data.get("protocol_repair"):
                check.update(applicable=True, status="covered", answer_quote="invented witness")

    transport = ReviewTransport(partial)
    with performance_scope():
        result = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
            "保修期多久？", draft, sources
        )
        events = performance_report()["events"]
    assert not result.supported
    assert len(result.condition_checks) == 93
    assert result.condition_checks[0]["status"] == "missing"
    repair = transport.calls[2]
    assert [r["id"] for r in repair["condition_requirements"]] == ["K3"]
    assert [r["evidence_id"] for r in repair["sources"]] == ["E4", "E1"]
    assert result.condition_checks[2]["status"] == "not_applicable"
    retry_stages = [e for e in events if e.get("name") == "verification.conditions.protocol_repair"]
    assert len(retry_stages) == 1
    assert retry_stages[0]["condition_count"] == 1


@pytest.mark.parametrize("count", [3, 93])
@pytest.mark.parametrize("shape", ["omitted", "duplicate", "reordered"])
def test_condition_id_recovery_retains_valid_missing_verdicts_and_only_rechecks_gap(
    tmp_path, count, shape
):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(count)

    def partial(data, response, kwargs):
        checks = response["condition_checks"]
        for check in checks:
            if check["id"] == "K1":
                check.update(
                    applicable=True, status="missing", reason="Required prerequisite is absent."
                )
        if not data.get("protocol_repair"):
            if shape == "omitted":
                response["condition_checks"] = [c for c in checks if c["id"] != "K3"]
            elif shape == "duplicate":
                response["condition_checks"] = checks + [
                    c.copy() for c in checks if c["id"] == "K3"
                ]
            else:
                response["condition_checks"] = list(reversed(checks))

    transport = ReviewTransport(partial)
    result = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        "完整适用条件？", draft, sources
    )
    assert not result.supported
    assert len(result.condition_checks) == count
    assert result.condition_checks[0]["status"] == "missing"
    repairs = [c for c in transport.calls if c.get("protocol_repair")]
    if shape == "reordered":
        assert repairs == []
    else:
        assert len(repairs) == 1
        assert [r["id"] for r in repairs[0]["condition_requirements"]] == ["K3"]
    initial = transport.calls[0]
    assert len(initial["conflict_evidence"]) == len(sources)


def test_unknown_condition_id_cannot_be_accepted_as_coverage(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample()

    def invalid(data, response, kwargs):
        if response["condition_checks"]:
            response["condition_checks"].append({**response["condition_checks"][0], "id": "K999"})

    with pytest.raises(InvalidProviderResponse, match="VERIFIER_CONDITION_CHECK_REQUIRED"):
        OpenAICompatibleClaimVerifier(settings, transport=ReviewTransport(invalid)).verify(
            "保修期限？", draft, sources
        )


def test_contradictory_applicability_reason_requires_provider_correction(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample()

    def contradictory(data, response, kwargs):
        for check in response["condition_checks"]:
            if check["id"] == "K81":
                check["reason"] = "Question asks duration; this repair rule is outside that scope."
                if not data.get("protocol_repair"):
                    check.update(applicable=True, status="missing")

    transport = ReviewTransport(contradictory)
    result = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        "保修期多久？", draft, sources
    )
    assert result.supported
    assert len(result.condition_checks) == 93
    assert transport.calls[-1]["protocol_repair"]["reason"] == "applicability_reason_mismatch"
    assert [r["id"] for r in transport.calls[-1]["condition_requirements"]] == ["K81"]

    with pytest.raises(InvalidProviderResponse, match="VERIFIER_CONDITION_VERDICT_INVALID"):
        OpenAICompatibleClaimVerifier(
            settings, transport=ReviewTransport(contradictory), condition_protocol_repair=False
        ).verify("保修期多久？", draft, sources)


def test_uncited_conflict_at_end_of_large_pool_is_preserved(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample()
    conflict = _evidence(
        evidence_id="E95",
        chunk_id="opposing",
        document_id="opposing",
        text="设备保修期为一年。",
        source_role="conflict_context",
    )

    def conflict_response(data, response, kwargs):
        assert data["conflict_evidence"][-1]["evidence_id"] == "E95"
        response["conflict_check"] = {
            "checked": True,
            "conflicting_evidence_ids": ["E1", "E95"],
            "pairs": [
                {
                    "left_id": "E1",
                    "left_quote": sources[0].text,
                    "right_id": "E95",
                    "right_quote": conflict.text,
                    "reason": "Same device and warranty scope have incompatible durations.",
                }
            ],
        }

    transport = ReviewTransport(conflict_response)
    result = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        "保修期多久？", draft, sources + (conflict,)
    )
    assert not result.supported
    assert result.conflicting_evidence_ids == ("E1", "E95")
    assert len(transport.calls) == 1


@pytest.mark.parametrize("outer", [False, True])
def test_batching_preserves_total_and_shorter_caller_deadlines(tmp_path, monkeypatch, outer):
    import ragkb.application.deadlines as deadlines

    settings, _ = _settings(tmp_path)
    settings = settings.model_copy(update={"verifier_total_timeout_seconds": 100 if outer else 10})
    draft, sources = sample()
    clock = [100.0]
    monkeypatch.setattr(deadlines.time, "monotonic", lambda: clock[0])

    def advance(data, response, kwargs):
        clock[0] += 4

    transport = ReviewTransport(advance)
    model = OpenAICompatibleClaimVerifier(settings, transport=transport)
    with pytest.raises(ProviderTimeout) as caught, request_deadline(10 if outer else 100):
        model.verify("保修期多久？", draft, sources)
    assert caught.value.code == "VERIFIER_TIMEOUT"
    assert caught.value.diagnostic["verification_stage"] == "conditions"
    assert caught.value.diagnostic["batch_number"] == 2
    assert caught.value.diagnostic["completed_batches"] == 1
    assert len(transport.calls) == 3


def test_capacity_limit_fails_before_calls_without_dropping_conditions(tmp_path):
    settings, _ = _settings(tmp_path)
    settings = settings.model_copy(update={"verifier_max_condition_batches": 2})
    draft, sources = sample()
    transport = ReviewTransport()
    with pytest.raises(InvalidProviderResponse) as caught:
        OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
            "保修期？", draft, sources
        )
    assert caught.value.code == "VERIFIER_CONDITION_BUDGET_EXCEEDED"
    assert caught.value.diagnostic["condition_count"] == 93
    assert caught.value.diagnostic["batch_count"] == 6
    assert transport.calls == []


def test_oversized_single_condition_fails_without_truncation(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(1)
    sources = (sources[0], replace(sources[1], text="维修仅限" + "本市" * 3100 + "。"))
    transport = ReviewTransport()
    with pytest.raises(InvalidProviderResponse) as caught:
        OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
            "保修期？", draft, sources
        )
    assert caught.value.diagnostic["reason"] == "single_condition_too_large"
    assert caught.value.diagnostic["condition_id"] == "K1"
    assert transport.calls == []


def test_timeout_keeps_private_diagnostics_and_public_reason_without_draft(tmp_path):
    _, sources = sample(0)
    service, repository, _ = _service(tmp_path, SyntheticEvidenceProvider(sources))

    class TimeoutVerifier:
        revision = "timeout-test"

        def verify(self, *args):
            raise ProviderTimeout(
                "VERIFIER_TIMEOUT", diagnostic={"batch_number": 3, "provider_code": "PRIVATE_CODE"}
            )

    service.verifier = TimeoutVerifier()
    result = service.ask("保修期多久？", "tenant-1", "user-1")
    assert not result.verified and result.answer is None
    assert result.retryable and "CLAIM_VERIFIER_TIMEOUT" in result.warnings
    assert result.coverage_report["verification_failure"] == {
        "batch_number": 3,
        "stage": "claims_and_conflicts",
    }
    assert repository.get_package(result.rag_run_id).diagnostics["failure"]["detail"] == {
        "verification_stage": "claims_and_conflicts",
        "batch_number": 3,
        "provider_code": "PRIVATE_CODE",
    }
