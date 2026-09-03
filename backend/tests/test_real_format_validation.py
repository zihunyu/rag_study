from __future__ import annotations

from pathlib import Path

from ragkb.evaluation.real_format_validation import build_real_format_validation


def test_real_format_validation_totals_and_new_embedding_attempt_are_exact() -> None:
    root = Path(__file__).resolve().parents[2]
    report = build_real_format_validation(root)
    assert report["totals"] == {
        "sample_count": 50,
        "chunk_count": 1128,
        "expected_locator_count": 78,
        "matched_locator_count": 78,
    }
    assert report["by_format"]["pdf_text"]["chunk_count"] == 25
    assert report["by_format"]["pptx"]["chunk_count"] == 385
    assert report["by_format"]["spreadsheet"]["chunk_count"] == 259
    assert report["by_format"]["pdf_scanned_or_image"]["chunk_count"] == 157
    assert report["by_format"]["docx"]["chunk_count"] == 302
    embedding = report["embedding_coverage"]
    assert embedding["completed_chunks"] == 1128
    assert embedding["uncovered_chunks"] == 0
    assert embedding["new_attempt"]["max_batches"] == 46
    assert embedding["new_attempt"]["approved_by_user"] is True
    assert embedding["new_attempt"]["runner_review_required_before_execution"] is False
    assert embedding["new_attempt"]["approved"] is True
    assert embedding["new_attempt"]["executed"] is True
    assert embedding["new_attempt"]["completed_batches"] == 46
    assert embedding["new_attempt"]["vector_count"] == 459
    assert embedding["new_attempt"]["automatic_retries"] == 0
    assert embedding["new_attempt"]["zilliz_write_approved"] is False
    assert (
        root / "artifacts/final-validation/provider-checkpoints/"
        "embedding-format-remainder-attempt-v3.json"
    ).exists()
    assert report["uat"]["candidate_count"] == 78
    assert report["uat"]["status"] == "APPROVED_BY_USER"
    assert report["uat"]["pending_snapshot_unchanged"] is True
    assert report["uat"]["model_execution_plan_ready"] is True
    assert report["format_quality_ready"] is True
    assert report["real_acceptance"] is True
    assert report["external_call_count_this_stage"] == 46
    assert report["content_output"] is False
    assert report["source_names_output"] is False
