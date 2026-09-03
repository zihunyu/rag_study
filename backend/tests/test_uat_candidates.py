from __future__ import annotations

from pathlib import Path

import pytest
from ragkb.evaluation.uat_candidates import (
    approve_all_uat_candidates,
    generate_uat_candidates,
    require_user_review_before_model_calls,
)


def test_uat_candidates_are_content_free_stable_and_block_models_before_review() -> None:
    root = Path(__file__).resolve().parents[2]
    first = generate_uat_candidates(
        root, root / "backend/tests/fixtures/manifests/format-samples.yaml"
    )
    second = generate_uat_candidates(
        root, root / "backend/tests/fixtures/manifests/format-samples.yaml"
    )

    assert first == second
    assert first["candidate_count"] > 0
    assert first["model_call_count"] == first["network_call_count"] == 0
    assert first["all_pending_user_review"] is True
    assert all(item["uses_source_content"] is False for item in first["candidates"])
    assert all(item["uses_source_filename"] is False for item in first["candidates"])
    with pytest.raises(ValueError, match="USER_REVIEW_REQUIRED"):
        require_user_review_before_model_calls(first)


def test_frozen_uat_snapshot_approves_all_without_changing_candidate_evidence() -> None:
    root = Path(__file__).resolve().parents[2]
    pending_path = root / "artifacts/final-validation/uat-candidates/pending-review.json"
    pending_bytes = pending_path.read_bytes()
    approved, manifest = approve_all_uat_candidates(
        pending_bytes,
        "fee7e5931d0930f3c8a2f29786abdbf791592d92e2dfc7c355688d965d7558b2",
    )
    import json

    pending = json.loads(pending_bytes)
    assert len(approved["candidates"]) == 78
    for before, after in zip(pending["candidates"], approved["candidates"], strict=True):
        assert after["status"] == "APPROVED_BY_USER"
        for field in (
            "candidate_id",
            "question",
            "expected_locator",
            "expected_evidence",
        ):
            assert after[field] == before[field]
    require_user_review_before_model_calls(approved)
    assert manifest["decision"] == "APPROVE_ALL_78"
    assert manifest["candidate_count"] == 78
    assert manifest["question_text_in_manifest"] is False
    assert "question" not in manifest
    assert manifest["network_call_count"] == 0
    assert pending_path.read_bytes() == pending_bytes
    with pytest.raises(ValueError, match="SNAPSHOT_MISMATCH"):
        approve_all_uat_candidates(pending_bytes, "0" * 64)
