"""Shared helpers for secret-safe technical evidence."""

from __future__ import annotations

from collections.abc import Mapping


def result(
    spike: str,
    assertions: list[dict[str, object]],
    blockers: list[str],
    metrics: Mapping[str, object] | None = None,
) -> dict[str, object]:
    harness_passed = all(bool(item["passed"]) for item in assertions)
    return {
        "report_schema_version": 1,
        "spike": spike,
        "harness_passed": harness_passed,
        "real_gate_status": "BLOCKED" if blockers else "READY_FOR_REAL_REVIEW",
        "real_acceptance": False,
        "assertions": assertions,
        "metrics": dict(metrics or {}),
        "blockers": sorted(set(blockers)),
        "attestation": "Harness results are not real-service Gate acceptance evidence.",
    }
