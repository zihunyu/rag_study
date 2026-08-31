from __future__ import annotations

from pathlib import Path

from ragkb.config.loader import load_configuration
from ragkb.spikes.capacity import run_capacity_spike
from ragkb.spikes.milvus import run_milvus_spike
from ragkb.spikes.mineru import run_mineru_spike
from ragkb.spikes.models import run_model_spike
from ragkb.spikes.security import run_security_spike


def test_all_g0_harnesses_pass_but_do_not_claim_real_acceptance() -> None:
    loaded = load_configuration()
    root = loaded.repository_root
    reports = [
        run_mineru_spike(loaded, root / "config/spikes/mineru-samples.yaml"),
        run_milvus_spike(loaded),
        run_model_spike(loaded),
        run_capacity_spike(loaded),
        run_security_spike(loaded),
    ]

    assert all(report["harness_passed"] for report in reports)
    assert all(report["real_acceptance"] is False for report in reports)
    assert all(report["real_gate_status"] == "BLOCKED" for report in reports)
    assert all(report["blockers"] for report in reports)


def test_mineru_empty_manifest_reports_exact_sample_deficit(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("schema_version: 1\nsamples: []\n", encoding="utf-8")

    report = run_mineru_spike(load_configuration(), manifest)

    assert report["metrics"]["required_real_samples"] == 60
    assert report["metrics"]["provided_real_samples"] == 0
    assert any("pdf_text" in blocker for blocker in report["blockers"])


def test_repository_manifest_has_g0_sixty_slot_plan_without_claiming_support() -> None:
    loaded = load_configuration()
    report = run_mineru_spike(loaded, loaded.repository_root / "config/spikes/mineru-samples.yaml")

    assert report["harness_passed"] is True
    assert report["metrics"]["planned_sample_slots"] == 60
    assert report["metrics"]["provided_real_samples"] == 0
    assert report["real_acceptance"] is False
