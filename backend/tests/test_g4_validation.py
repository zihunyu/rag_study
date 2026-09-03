from __future__ import annotations

from pathlib import Path

from ragkb.application.resilience import CircuitState, DryRunCostMeter, LocalCircuitBreaker
from ragkb.evaluation.g4_validation import build_g4_local_validation_report
from ragkb.evaluation.prompt_injection import run_prompt_injection_cases


def test_g4_local_validation_report_is_measured_context_not_real_slo_claim() -> None:
    root = Path(__file__).resolve().parents[2]
    report = build_g4_local_validation_report(root)

    assert report["local_preparation_ready"] is True
    assert report["real_acceptance"] is False
    assert report["real_external_call_performed"] is False
    injection = report["security"]["prompt_injection"]
    assert injection["passed_count"] == injection["case_count"] == 8
    assert set(injection["boundary_counts"].values()) == {0}
    system = report["performance"]["representative_system_paths"]
    assert system["failure_count"] == 0
    assert system["slo_claimed"] is False
    assert {item["document_count"] for item in system["scales"]} == {1, 5, 20}
    assert system["performance_scope"] == [1, 5, 20]
    assert system["statistical_confidence"] == "low"
    assert report["backup_restore"]["tombstone_replayed_first"] is True
    assert report["backup_restore"]["deleted_document_visible_after_restore"] is False
    assert report["backup_restore"]["reference_revocation_preserved"] is True
    assert report["backup_restore"]["file_hashes_preserved"] is True


def test_cost_meter_and_circuit_breaker_are_deterministic_dry_run_contracts() -> None:
    meter = DryRunCostMeter()
    meter.record(input_units=10, output_units=2)
    breaker = LocalCircuitBreaker(2)
    breaker.record_failure()
    breaker.record_failure()
    assert meter.report()["billable_request_performed"] is False
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False
    breaker.begin_probe()
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


def test_prompt_injection_runner_executes_all_security_contracts() -> None:
    root = Path(__file__).resolve().parents[2]
    report = run_prompt_injection_cases(root)

    assert report["passed_count"] == report["case_count"] == 8
    assert all(item["actual"] == item["expected"] for item in report["results"])
    assert report["boundary_counts"] == {
        "hidden_retrieval": 0,
        "cross_tenant_text": 0,
        "forged_citation": 0,
        "external_egress": 0,
    }
