from __future__ import annotations

import hashlib

from scripts.prepare_docx_pdf_inputs import validate_prepared_docx_pdf_inputs
from scripts.run_mineru_provider import ATTEMPTS, ROOT, build_attempt_plan


def test_ten_prepared_docx_pdfs_cover_expected_pages_and_plan_is_offline() -> None:
    report = validate_prepared_docx_pdf_inputs()
    assert report["converted_count"] == 10
    assert report["derived_hash_count"] == 10
    assert report["expected_pages_covered_count"] == 10
    assert int(report["page_count_min"]) >= 2
    assert report["source_snapshots_preserved"] is True
    assert report["anonymous_paths"] is True
    assert report["network_call_performed"] is False
    assert report["source_names_output"] is False
    assert report["content_output"] is False

    attempt = ATTEMPTS["execute-docx-pdf-v1"]
    assert attempt["scope"] == "docx_pdf"
    assert attempt["max_files"] == 10
    assert attempt["expected_locator_count"] == 20
    assert attempt["approved_by_user"] is True
    plan = build_attempt_plan()
    planned = next(
        item
        for item in plan["attempts"]
        if item["attempt_revision"] == "mineru-docx-pdf-attempt:v1"
    )
    assert planned["executed"] is True
    assert planned["execution_status"] == "COMPLETED"
    assert planned["completed_files"] == 10
    assert planned["approved_by_user"] is True


def test_historical_docx_and_scan_checkpoints_remain_byte_identical() -> None:
    checkpoint_root = ROOT / "artifacts/final-validation/provider-checkpoints"
    expected = {
        "mineru.json": "f64a00e00747fa7d9a1f97dde530da07dd63ed060d4f6d6bd810b04c4f9da3f0",
        "mineru-scan-attempt-v2.json": (
            "6f3f21ed74c55c4a57afdc4cbf5455b28a470b81198af7fbdf2bad6db39a982a"
        ),
        "mineru-scan-attempt-v3.json": (
            "a6fe1d1dd651c938d847acbe9181e5291e50dcc79f1906f20ff70eee2b6cc452"
        ),
        "mineru-scan-attempt-v4.json": (
            "182e4a4811d4708074a4c39fd522d5cf011e8955bef489bd22021a98fa402b07"
        ),
        "mineru-scan-attempt-v5.json": (
            "71200ca9a76c9655e043886f6e5e996223584e534cadbfca99e3c883fa2678e7"
        ),
        "mineru-docx-attempt-v1.json": (
            "14df78adbf4b52bbfd69aeafce3b58fb1d79c7d55bf9a343766b9fbbdc07a20e"
        ),
        "mineru-docx-recovery-v1.json": (
            "61dbd8933f56c0f3d82407011d12e745a3a1634c54e30f7fe49f0636d96a14af"
        ),
    }
    assert all(
        hashlib.sha256((checkpoint_root / name).read_bytes()).hexdigest() == digest
        for name, digest in expected.items()
    )
