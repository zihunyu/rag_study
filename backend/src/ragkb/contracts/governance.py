"""Persistence port for observability and simulated governance preparation."""

from __future__ import annotations

from typing import Protocol


class GovernanceRepositoryPort(Protocol):
    def record_event(
        self, trace_id: str, event_type: str, severity: str, payload: dict[str, object]
    ) -> str: ...

    def diagnostics(self) -> dict[str, object]: ...

    def create_pilot(self, name: str, feature_flag: str) -> dict[str, object]: ...

    def get_pilot(self, pilot_id: str) -> dict[str, object]: ...

    def update_pilot(
        self,
        pilot_id: str,
        state: str,
        blockers: list[str],
        expected_revision: int | None = None,
    ) -> dict[str, object]: ...

    def record_canary(
        self,
        pilot_id: str,
        seed: int,
        request_count: int,
        failure_count: int,
        threshold: int,
        expected_revision: int | None = None,
    ) -> dict[str, object]: ...

    def latest_canary(self, pilot_id: str) -> dict[str, object] | None: ...

    def add_signoff(
        self,
        scope_type: str,
        scope_id: str,
        role: str,
        decision: str,
        signer_id: str,
        comment: str,
    ) -> dict[str, object]: ...

    def latest_signoffs(self, scope_type: str, scope_id: str) -> dict[str, str]: ...

    def add_rollout_batch(
        self, pilot_id: str, ordinal: int, percentage: int
    ) -> dict[str, object]: ...

    def update_uat_case(
        self,
        case_id: str,
        result: str,
        evidence: list[dict[str, str]],
        step_results: list[str],
        expected_row_version: int,
    ) -> dict[str, object]: ...

    def pilot_uat_status(self, pilot_id: str) -> dict[str, object]: ...

    def evidence_reference_exists(self, reference: dict[str, str]) -> bool: ...

    def open_critical_defects(self, scope_type: str, scope_id: str) -> list[str]: ...

    def get_observation(self, window_id: str) -> dict[str, object]: ...

    def create_observation(self, name: str, starts_at: float) -> dict[str, object]: ...

    def close_observation(self, window_id: str, expected_row_version: int) -> dict[str, object]: ...

    def open_critical_incidents(self, window_id: str) -> list[str]: ...
