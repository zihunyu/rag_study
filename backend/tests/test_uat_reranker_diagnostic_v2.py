from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from ragkb.application.uat_provider_runners import UatRerankerDiagnosticV2Runner
from ragkb.contracts.provider_execution import ProviderExecutionError
from ragkb.evaluation.uat_diagnostic_v2 import build_candidate2_diagnostic_v2
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore


class _DiagnosticReranker:
    real_network = False

    def __init__(self, *, gate_passed: bool = True) -> None:
        self.gate_passed = gate_passed
        self.calls: list[str] = []

    def rerank(self, query, documents, top_n, idempotency_key, timeout_seconds):
        del query, top_n, timeout_seconds
        self.calls.append(idempotency_key)
        order = list(range(len(documents)))
        return order if self.gate_passed else order[1:] + order[:1]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _sources() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    documents = [
        {
            "evidence_id": f"evidence-{index}",
            "role": "positive" if index == 0 else "distractor",
            "locator": {"page": index + 1},
            "content": f"authorized evidence {index}",
            "content_sha256": _hash(f"authorized evidence {index}"),
        }
        for index in range(4)
    ]
    original = {
        "revision": "locator-grounded-uat-bundle:v2",
        "candidate_id": "0123456789abcdefabcd",
        "question": "original question?",
        "source_category": "pdf_text",
        "source_classification": "internal",
        "expected_locator": {"page": 1},
        "expected_evidence": {"anonymous_sample_id": "sample"},
        "expected_positive_evidence_id": "evidence-0",
        "documents": documents,
    }
    failure = {
        "candidate_id": original["candidate_id"],
        "question": original["question"],
        "documents": [
            {
                key: document[key]
                for key in ("evidence_id", "role", "locator", "content", "content_sha256")
            }
            for document in documents
        ],
    }
    proposals = {
        "candidate_id": original["candidate_id"],
        "proposals": [
            {"proposal_number": 1, "question": "revised question one?"},
            {"proposal_number": 2, "question": "revised question two?"},
            {"proposal_number": 3, "question": "revised question three?"},
        ],
    }
    return proposals, failure, original


def _diagnostic_bundle() -> tuple[dict[str, object], dict[str, object]]:
    proposals, failure, original = _sources()
    return build_candidate2_diagnostic_v2(
        proposals,
        failure,
        original,
        proposal_sha256="a" * 64,
        failure_review_sha256="b" * 64,
        reranker_v1_sha256="c" * 64,
        original_bundle_sha256="d" * 64,
    )


def test_candidate2_v2_changes_only_question_and_binds_frozen_inputs() -> None:
    proposals, failure, original = _sources()
    revision, bundle = _diagnostic_bundle()
    assert revision["question"] == proposals["proposals"][0]["question"]
    assert revision["question"] != original["question"]
    assert bundle["documents"] == original["documents"]
    assert bundle["expected_positive_evidence_id"] == original["expected_positive_evidence_id"]
    assert bundle["manifest"]["original_candidate_id"] == failure["candidate_id"]
    assert bundle["manifest"]["proposal_artifact_sha256"] == "a" * 64
    assert bundle["max_requests"] == 1
    assert bundle["automatic_retries"] == 0
    assert bundle["llm_allowed"] is False


def test_v2_pass_uses_one_request_and_resume_does_not_duplicate(tmp_path: Path) -> None:
    _, bundle = _diagnostic_bundle()
    checkpoints = JsonCheckpointStore(tmp_path / "v2.json")
    transport = _DiagnosticReranker()
    runner = UatRerankerDiagnosticV2Runner(transport, checkpoints, external_call_approved=False)
    result = runner.run(bundle)
    assert result["request_count"] == result["completed_count"] == 1
    assert result["gate_passed_count"] == 1
    assert result["llm_request_count"] == 0
    assert len(transport.calls) == 1
    runner.run(bundle)
    assert len(transport.calls) == 1
    serialized = checkpoints.path.read_text(encoding="utf-8")
    assert '"question"' not in serialized
    assert '"content"' not in serialized


def test_v2_failure_persists_order_rank_and_never_retries(tmp_path: Path) -> None:
    _, bundle = _diagnostic_bundle()
    checkpoints = JsonCheckpointStore(tmp_path / "v2-failed.json")
    transport = _DiagnosticReranker(gate_passed=False)
    runner = UatRerankerDiagnosticV2Runner(transport, checkpoints, external_call_approved=False)
    with pytest.raises(ProviderExecutionError, match="POSITIVE_NOT_IN_TOP_K"):
        runner.run(bundle)
    assert len(transport.calls) == 1
    loaded = json.loads(checkpoints.path.read_text(encoding="utf-8"))["uat_reranker_v2"]
    records = [value for key, value in loaded.items() if key != "_manifest"]
    assert len(records) == 1
    assert records[0]["state"] == "FAILED"
    assert records[0]["gate_passed"] is False
    assert records[0]["positive_rank"] == 4
    assert records[0]["response_index_count"] == 4
    assert len(records[0]["ranked_evidence_ids"]) == 4
    assert records[0]["automatic_retries"] == 0
    with pytest.raises(ProviderExecutionError, match="POSITIVE_NOT_IN_TOP_K"):
        runner.run(bundle)
    assert len(transport.calls) == 1
    serialized = checkpoints.path.read_text(encoding="utf-8")
    assert '"question"' not in serialized
    assert '"content"' not in serialized


def test_v2_rejects_non_exact_budget_before_request(tmp_path: Path) -> None:
    _, bundle = _diagnostic_bundle()
    transport = _DiagnosticReranker()
    with pytest.raises(ProviderExecutionError, match="EXECUTION_PARAMETERS_INVALID"):
        UatRerankerDiagnosticV2Runner(
            transport,
            JsonCheckpointStore(tmp_path / "invalid-budget.json"),
            external_call_approved=False,
            max_requests=2,
        ).run(bundle)
    assert transport.calls == []
