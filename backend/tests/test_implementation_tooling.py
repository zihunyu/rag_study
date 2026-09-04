from __future__ import annotations

from pathlib import Path

from ragkb.application.governance import GovernanceService

from scripts.generate_assurance import build_assurance
from scripts.generate_final_validation_plan import build_plan as build_final_plan
from scripts.plan_operations import build_plan as build_operations_plan


def test_operations_assurance_and_final_plan_are_offline_and_fail_closed() -> None:
    operations = build_operations_plan()
    assurance = build_assurance()
    final = build_final_plan()
    canary = GovernanceService.synthetic_canary(20260901)

    assert operations["external_mutation_performed"] is False
    assert operations["simulated"] is True
    assert assurance["network_scan_performed"] is False
    assert assurance["docker_used"] is False
    assert assurance["python_sbom"]
    assert assurance["npm_sbom"]
    assert assurance["cyclonedx_sbom"]["bomFormat"] == "CycloneDX"
    assert assurance["cyclonedx_sbom"]["specVersion"] == "1.6"
    assert final["status"] == "BLOCKED_REAL_EVIDENCE_MISSING"
    assert final["synthetic_evidence_can_unlock"] is False
    assert final["real_acceptance"] is False
    assert final["real_format_acceptance"] is True
    assert "non_asr_real_formats_5x10" in final["completed_suites"]
    assert "REAL_FORMAT_SAMPLES_NON_ASR_5_X_10_REQUIRED" not in final["blockers"]
    assert "seven_day_observation" not in final["suites"]
    assert final["scope"]["real_7_day_observation"] == "deferred_by_user"
    assert canary["simulated"] is True
    assert canary["real_acceptance"] is False


def test_containers_are_digest_pinned_non_root_health_checked_and_fully_scanned() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfiles = (
        root / "Dockerfile.backend",
        root / "Dockerfile.worker",
        root / "frontend/Dockerfile",
    )
    for path in dockerfiles:
        text = path.read_text(encoding="utf-8")
        assert "@sha256:" in text
        assert "USER " in text
        assert "HEALTHCHECK " in text
    workflow = (root / ".github/workflows/container-security.yml").read_text(encoding="utf-8")
    assert "name: backend" in workflow
    assert "name: worker" in workflow
    assert "name: frontend" in workflow
    assert "format: cyclonedx" in workflow
    assert "--require-hashes" in (root / "Dockerfile.backend").read_text(encoding="utf-8")
    assert "--hash=sha256:" in (root / "requirements.lock").read_text(encoding="utf-8")
    assert (root / "LICENSE").is_file()
    assert (root / "NOTICE").is_file()
    nginx = (root / "frontend/nginx.conf").read_text(encoding="utf-8")
    assert "client_max_body_size 200m" in nginx
    assert "client_body_buffer_size 1m" in nginx
