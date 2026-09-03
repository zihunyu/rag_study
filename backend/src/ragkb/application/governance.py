"""Pilot, rollout, UAT and final-acceptance preparation orchestration."""

from __future__ import annotations

import time
from collections.abc import Callable

from ragkb.contracts.governance import GovernanceRepositoryPort
from ragkb.domain.governance import FINAL_REAL_EVIDENCE_REQUIREMENTS, PilotState, ReadinessResult

PILOT_SIGNOFF_ROLES = ("technical", "security", "sre")
FINAL_SIGNOFF_ROLES = ("business", "technical", "security", "operations")


class GovernanceService:
    revision = "governance-preparation:g5-g6-v2"

    def __init__(
        self, repository: GovernanceRepositoryPort, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.repository = repository
        self.clock = clock

    def evaluate_pilot(
        self, pilot_id: str, expected_revision: int | None = None
    ) -> ReadinessResult:
        signoffs = self.repository.latest_signoffs("pilot", pilot_id)
        blockers = [
            f"SIGNOFF_MISSING:{role}" for role in PILOT_SIGNOFF_ROLES if role not in signoffs
        ]
        blockers.extend(f"VETO:{role}" for role, decision in signoffs.items() if decision == "VETO")
        blockers.extend(
            f"CRITICAL_DEFECT:{defect_id}"
            for defect_id in self.repository.open_critical_defects("pilot", pilot_id)
        )
        canary = self.repository.latest_canary(pilot_id)
        if canary is None:
            blockers.append("CANARY_REQUIRED")
        elif str(canary["result"]) != "PASS":
            blockers.append("CANARY_FAILED")
        uat = self.repository.pilot_uat_status(pilot_id)
        if not bool(uat["all_passed_with_evidence"]):
            blockers.append("UAT_SUITE_NOT_PASSED_WITH_EVIDENCE")
        state = PilotState.SIMULATED_GO if not blockers else PilotState.NO_GO
        self.repository.update_pilot(pilot_id, state.value, blockers, expected_revision)
        return ReadinessResult(pilot_id, state.value, tuple(blockers))

    def plan_rollout(
        self, pilot_id: str, expected_revision: int | None = None
    ) -> list[dict[str, object]]:
        pilot = self.repository.get_pilot(pilot_id)
        if str(pilot["state"]) != PilotState.SIMULATED_GO:
            raise ValueError("PILOT_NOT_READY_OR_ALREADY_ROLLED_OUT")
        batches = [
            self.repository.add_rollout_batch(pilot_id, ordinal, percentage)
            for ordinal, percentage in enumerate((5, 25, 50, 100), start=1)
        ]
        self.repository.update_pilot(pilot_id, PilotState.ROLLING_OUT.value, [], expected_revision)
        return batches

    def rollback_pilot(
        self, pilot_id: str, trigger: str, expected_revision: int | None = None
    ) -> dict[str, object]:
        if not trigger.strip():
            raise ValueError("ROLLBACK_TRIGGER_REQUIRED")
        pilot = self.repository.get_pilot(pilot_id)
        if str(pilot["state"]) == PilotState.ROLLED_BACK:
            return pilot
        return self.repository.update_pilot(
            pilot_id,
            PilotState.ROLLED_BACK.value,
            [f"ROLLBACK_TRIGGER:{trigger}"],
            expected_revision,
        )

    @staticmethod
    def synthetic_canary(seed: int, request_count: int = 20) -> dict[str, object]:
        failures = sum((seed + index) % 17 == 0 for index in range(request_count))
        return {
            "seed": seed,
            "request_count": request_count,
            "success_count": request_count - failures,
            "failure_count": failures,
            "feature_flag": "synthetic-pilot",
            "rollback_triggered": failures > 2,
            "simulated": True,
            "real_acceptance": False,
        }

    def run_canary(
        self,
        pilot_id: str,
        seed: int,
        request_count: int = 20,
        threshold: int = 2,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        result = self.synthetic_canary(seed, request_count)
        return self.repository.record_canary(
            pilot_id,
            seed,
            request_count,
            int(str(result["failure_count"])),
            threshold,
            expected_revision,
        )

    def create_observation(self, name: str) -> dict[str, object]:
        return self.repository.create_observation(name, self.clock())

    def close_observation(self, window_id: str, expected_row_version: int) -> dict[str, object]:
        observation = self.repository.get_observation(window_id)
        if self.clock() < float(str(observation["ends_at"])):
            raise ValueError("OBSERVATION_SEVEN_DAYS_NOT_ELAPSED")
        metrics = observation["metrics"]
        required = {
            "availability",
            "error_rate",
            "latency_p95",
            "sample_count",
            "coverage_ratio",
            "sampling_gap_count",
        }
        if not isinstance(metrics, dict) or not required.issubset(metrics):
            raise ValueError("OBSERVATION_REQUIRED_METRICS_MISSING")
        if (
            float(str(metrics["sample_count"])) <= 0
            or float(str(metrics["coverage_ratio"])) < 1.0
            or float(str(metrics["sampling_gap_count"])) > 0
        ):
            raise ValueError("OBSERVATION_METRIC_COVERAGE_INCOMPLETE")
        return self.repository.close_observation(window_id, expected_row_version)

    def evaluate_observation(self, window_id: str) -> ReadinessResult:
        observation = self.repository.get_observation(window_id)
        signoffs = self.repository.latest_signoffs("observation", window_id)
        blockers = [
            f"SIGNOFF_MISSING:{role}" for role in FINAL_SIGNOFF_ROLES if role not in signoffs
        ]
        blockers.extend(f"VETO:{role}" for role, decision in signoffs.items() if decision == "VETO")
        blockers.extend(
            f"CRITICAL_DEFECT:{item}"
            for item in self.repository.open_critical_defects("observation", window_id)
        )
        blockers.extend(
            f"CRITICAL_INCIDENT:{item}"
            for item in self.repository.open_critical_incidents(window_id)
        )
        if str(observation["state"]) != "CLOSED":
            blockers.append("OBSERVATION_NOT_CLOSED")
        if self.clock() < float(str(observation["ends_at"])):
            blockers.append("OBSERVATION_SEVEN_DAYS_NOT_ELAPSED")
        if not isinstance(observation["metrics"], dict) or not observation["metrics"]:
            blockers.append("OBSERVATION_METRICS_MISSING")
        state = "SIMULATED_COMPLETE" if not blockers else "BLOCKED"
        return ReadinessResult(window_id, state, tuple(blockers))

    def final_acceptance_report(self, window_id: str) -> dict[str, object]:
        observation = self.evaluate_observation(window_id)
        blockers = [*observation.blockers, *FINAL_REAL_EVIDENCE_REQUIREMENTS]
        return {
            "window_id": window_id,
            "status": "BLOCKED",
            "blockers": blockers,
            "synthetic_readiness_state": observation.state,
            "simulated": True,
            "real_acceptance": False,
            "generator_revision": self.revision,
        }
