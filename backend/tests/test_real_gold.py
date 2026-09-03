from __future__ import annotations

import pytest
from ragkb.evaluation.real_gold import sign_gold_dataset, validate_real_gold_dataset


def _dataset() -> dict[str, object]:
    query_types = (
        "identifier",
        "keyword",
        "semantic",
        "multihop",
        "temporal",
        "negative",
        "unanswerable",
        "permission",
        "semantic",
        "permission",
    )
    return {
        "schema_version": 2,
        "dataset_id": "business-gold",
        "revision": "v1",
        "status": "APPROVED",
        "reviewer_id": "business-reviewer",
        "reviewed_at": "2026-09-03T00:00:00Z",
        "corpus": [
            {
                "chunk_id": f"evidence-{index}",
                "document_id": f"document-{index}",
                "document_version_id": f"version-{index}",
                "text": f"approved evidence {index}",
                "locator": {"page": 1},
            }
            for index in range(1, 21)
        ],
        "cases": [
            {
                "case_id": f"case-{index}",
                "query_type": query_type,
                "question": f"approved question {index}",
                "expected_answer": "approved answer",
                "allowed_evidence_ids": [f"evidence-{index}"],
                "forbidden_evidence_ids": [],
                "principal": {
                    "tenant_id": "tenant",
                    "user_id": "user",
                    "clearance_level": 1,
                    "scope_tokens": ["group:reader"],
                },
                "expected_status": "answered",
                "performance_scale": 1 if index == 1 else 5 if index <= 5 else 20,
            }
            for index, query_type in enumerate(query_types, start=1)
        ],
    }


def test_exactly_ten_business_signed_cases_unlock_real_gold() -> None:
    key = b"business-review-secret"
    dataset = _dataset()
    dataset["signature"] = sign_gold_dataset(dataset, key)

    report = validate_real_gold_dataset(dataset, key)

    assert report["case_count"] == 10
    assert report["business_approved"] is True


def test_starter_or_unsigned_dataset_cannot_unlock_real_gold() -> None:
    dataset = _dataset()
    dataset["status"] = "PENDING_BUSINESS_REVIEW"
    with pytest.raises(ValueError, match="NOT_BUSINESS_APPROVED"):
        validate_real_gold_dataset(dataset, b"business-review-secret")
