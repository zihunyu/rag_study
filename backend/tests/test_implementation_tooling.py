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
    frontend_dockerfile = (root / "frontend/Dockerfile").read_text(encoding="utf-8")
    static_server = (root / "frontend/scripts/serve-dist.mjs").read_text(encoding="utf-8")
    assert "nginx" not in frontend_dockerfile.lower()
    assert "USER node" in frontend_dockerfile
    assert "FRONTEND_API_BASE_URL_REQUIRED" in static_server
    assert "runtime-config.js" in static_server


def test_runtime_and_development_dependency_locks_are_separated_and_reproducible() -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_lock = (root / "requirements.lock").read_text(encoding="utf-8")
    development_lock = (root / "requirements-dev.lock").read_text(encoding="utf-8")
    tools_lock = (root / "requirements-tools.lock").read_text(encoding="utf-8")
    compiler = (root / "scripts/compile_requirements.py").read_text(encoding="utf-8")

    for development_package in ("mypy==", "pytest==", "ruff=="):
        assert development_package not in runtime_lock
        assert development_package in development_lock
    assert "opentelemetry-sdk==" in runtime_lock
    assert "pip-tools==7.6.1" in tools_lock
    assert "setuptools==84.0.0" in tools_lock
    assert "wheel==0.48.0" in tools_lock
    assert 'PIP_TOOLS_VERSION = "7.6.1"' in compiler
