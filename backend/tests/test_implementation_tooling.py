from __future__ import annotations

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
