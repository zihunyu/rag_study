"""Safety checks for the isolated scheduling experiment, not a new QA release mode."""

from __future__ import annotations

import json
import threading
from collections import Counter

import pytest
from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
from ragkb.application.acceptance_budget import acceptance_budget, reserve_call
from ragkb.application.deadlines import remaining_timeout, request_deadline
from ragkb.domain.errors import InvalidProviderResponse
from ragkb.evaluation.verifier_parallelism import parallel_verify
from ragkb.infrastructure.model_account import operation, provider_operation
from test_batched_verifier import ReviewTransport, sample
from test_model_http_adapters import _settings
from test_trusted_qa import _evidence


@pytest.mark.parametrize("count", [60, 85, 93])
def test_schedule_preserves_requests_rules_and_results(tmp_path, count):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(count)
    serial, parallel = ReviewTransport(), ReviewTransport()
    baseline = parallel_verify(
        OpenAICompatibleClaimVerifier(settings, transport=serial),
        "保修期多久？",
        draft,
        sources,
        workers=1,
    )
    candidate = parallel_verify(
        OpenAICompatibleClaimVerifier(settings, transport=parallel),
        "保修期多久？",
        draft,
        sources,
        workers=3,
    )
    assert baseline == candidate
    assert len(candidate.condition_checks) == count
    # Balanced batches can change payload grouping, never source/rule content.
    assert serial.calls[0] == parallel.calls[0]  # complete conflict review
    assert Counter(
        json.dumps(r, sort_keys=True) for c in serial.calls[1:] for r in c["condition_requirements"]
    ) == Counter(
        json.dumps(r, sort_keys=True)
        for c in parallel.calls[1:]
        for r in c["condition_requirements"]
    )

    def original_sources(calls):
        return {s["evidence_id"]: s for c in calls[1:] for s in c["sources"]}

    assert original_sources(serial.calls) == original_sources(parallel.calls)
    for call in (*serial.calls[1:], *parallel.calls[1:]):
        for key in ("question", "answer", "answer_citation_ids", "answer_spans", "claims"):
            assert call[key] == serial.calls[1][key]


def test_workers_overlap_and_keep_parent_deadline_and_budget_context(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(48)
    barrier = threading.Barrier(3, timeout=3)
    lock = threading.Lock()
    calls = []

    def reserve():
        with lock:
            calls.append(operation.get())

    def context_check(data, response, kwargs):
        assert 0 < remaining_timeout(30) <= 2
        hook = reserve_call.get()
        assert hook is not None
        hook()
        if "conflict_evidence" not in data:
            barrier.wait()

    verifier = OpenAICompatibleClaimVerifier(settings, transport=ReviewTransport(context_check))
    with (
        request_deadline(2),
        acceptance_budget(reserve, lambda _: None),
        provider_operation("version", "asset", "latency_test"),
    ):
        assert parallel_verify(verifier, "保修期多久？", draft, sources).supported
    assert calls == [("version", "asset", "latency_test")] * 4


def test_missing_condition_cannot_be_hidden_by_later_successes(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(93)

    def missing(data, response, kwargs):
        for check in response["condition_checks"]:
            if check["id"] == "K37":
                check.update(applicable=True, status="missing", reason="Relevant rule is absent.")

    result = parallel_verify(
        OpenAICompatibleClaimVerifier(settings, transport=ReviewTransport(missing)),
        "维修政策？",
        draft,
        sources,
    )
    assert not result.supported
    assert len(result.condition_checks) == 93
    assert result.condition_checks[36]["status"] == "missing"


def test_last_batch_bad_witness_never_returns_partial_verified_result(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(93)

    def invalid(data, response, kwargs):
        for check in response["condition_checks"]:
            if check["id"] == "K93":
                check.update(applicable=True, status="covered", answer_quote="Invented text")

    with pytest.raises(InvalidProviderResponse):
        parallel_verify(
            OpenAICompatibleClaimVerifier(settings, transport=ReviewTransport(invalid)),
            "维修政策？",
            draft,
            sources,
        )


def test_full_pool_conflict_remains_blocking_before_workers_start(tmp_path):
    settings, _ = _settings(tmp_path)
    draft, sources = sample(93)
    conflict = _evidence(
        evidence_id="E95", text="设备保修期为一年。", source_role="conflict_context"
    )

    def opposing(data, response, kwargs):
        assert len(data["conflict_evidence"]) == 95
        response["conflict_check"] = {
            "checked": True,
            "conflicting_evidence_ids": ["E1", "E95"],
            "pairs": [
                {
                    "left_id": "E1",
                    "left_quote": sources[0].text,
                    "right_id": "E95",
                    "right_quote": conflict.text,
                    "reason": "Same warranty has incompatible durations.",
                }
            ],
        }

    transport = ReviewTransport(opposing)
    result = parallel_verify(
        OpenAICompatibleClaimVerifier(settings, transport=transport),
        "保修期多久？",
        draft,
        sources + (conflict,),
    )
    assert not result.supported and result.conflicting_evidence_ids == ("E1", "E95")
    assert len(transport.calls) == 1
