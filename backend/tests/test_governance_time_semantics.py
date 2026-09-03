from __future__ import annotations

from pathlib import Path

import pytest
from ragkb.application.governance import GovernanceService
from ragkb.infrastructure.governance_repository import SQLiteGovernanceRepository
from ragkb.infrastructure.sqlite import SQLiteDatabase


def test_observation_requires_actual_elapsed_time_metrics_close_and_signoffs(
    tmp_path: Path,
) -> None:
    now = [1_000.0]
    repository = SQLiteGovernanceRepository(SQLiteDatabase(tmp_path / "governance.sqlite3"))
    service = GovernanceService(repository, clock=lambda: now[0])
    observation = service.create_observation("fake clock window")
    window_id = str(observation["window_id"])

    assert service.evaluate_observation(window_id).state == "BLOCKED"
    with pytest.raises(ValueError, match="SEVEN_DAYS"):
        service.close_observation(window_id, 1)
    metrics = {
        "availability": 1.0,
        "error_rate": 0.0,
        "latency_p95": 0.01,
        "sample_count": 100.0,
        "coverage_ratio": 1.0,
        "sampling_gap_count": 0.0,
    }
    updated = repository.record_observation_metrics(window_id, metrics, 1)
    now[0] += 7 * 24 * 3600
    closed = service.close_observation(window_id, int(updated["row_version"]))
    assert closed["state"] == "CLOSED"
    assert service.evaluate_observation(window_id).state == "BLOCKED"
    for role in ("business", "technical", "security", "operations"):
        repository.add_signoff("observation", window_id, role, "APPROVE", role, "synthetic")

    result = service.evaluate_observation(window_id)

    assert result.state == "SIMULATED_COMPLETE"
    assert result.real_acceptance is False
