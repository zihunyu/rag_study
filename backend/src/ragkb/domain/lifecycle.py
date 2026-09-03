"""G3 lifecycle, security transition, tombstone and append-only audit contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class LifecycleState(StrEnum):
    DRAFT = "DRAFT"
    STAGED = "STAGED"
    SWITCHING = "SWITCHING"
    ACTIVE = "ACTIVE"
    SECURITY_TRANSITION = "SECURITY_TRANSITION"
    REVOKED = "REVOKED"
    DELETED = "DELETED"


class SecurityTransitionStatus(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class CleanupStatus(StrEnum):
    PENDING = "PENDING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


@dataclass
class LifecycleRecord:
    document_id: str
    active_version_id: str | None
    version_history: list[str] = field(default_factory=list)
    lifecycle_state: LifecycleState = LifecycleState.DRAFT
    acl_revision: int = 1
    visible: bool = False
    tombstoned: bool = False
    row_version: int = 1


@dataclass
class SecurityTransition:
    transition_id: str
    document_id: str
    target_acl_revision: int
    required_watermark: int
    status: SecurityTransitionStatus = SecurityTransitionStatus.PENDING
    observed_watermark: int = 0
    error_code: str | None = None


@dataclass
class DeletionTombstone:
    document_id: str
    cleanup: dict[str, CleanupStatus]


@dataclass(frozen=True)
class AuditEvent:
    sequence: int
    action: str
    resource_id: str
    trace_id: str
    governance_revision: str
    previous_hash: str
    event_hash: str
