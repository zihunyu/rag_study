"""Simulated pilot and final-acceptance preparation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PilotState(StrEnum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    SIMULATED_GO = "SIMULATED_GO"
    NO_GO = "NO_GO"
    ROLLING_OUT = "ROLLING_OUT"
    ROLLED_BACK = "ROLLED_BACK"


class Decision(StrEnum):
    APPROVE = "APPROVE"
    VETO = "VETO"


class DefectSeverity(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class RecordState(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    BLOCKED = "BLOCKED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class ReadinessResult:
    scope_id: str
    state: str
    blockers: tuple[str, ...]
    simulated: bool = True
    real_acceptance: bool = False


FINAL_REAL_EVIDENCE_REQUIREMENTS = (
    "REAL_FORMAT_SAMPLES_NON_ASR_5_X_10_REQUIRED",
    "REAL_MODEL_VALIDATION_REQUIRED",
    "MYSQL_G3_G4_MIGRATION_REQUIRED",
    "EXTERNAL_LIFECYCLE_DRILL_REQUIRED",
    "PRODUCTION_LIKE_PERFORMANCE_LONG_RUN_REQUIRED",
    "REAL_UAT_REQUIRED",
)
