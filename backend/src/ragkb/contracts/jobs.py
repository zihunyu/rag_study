"""Persistent job queue port used by application and Worker layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ragkb.domain.state_machines import JobState


@dataclass(frozen=True)
class QueueJob:
    id: str
    operation: str
    payload: dict[str, Any]
    idempotency_key: str
    request_hash: str
    state: JobState
    attempt: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: float | None
    heartbeat_at: float | None
    next_retry_at: float | None
    cancel_requested: bool
    error_code: str | None = None


class QueueConflictError(ValueError):
    pass


class QueueLeaseError(RuntimeError):
    pass


class PersistentJobQueuePort(Protocol):
    def enqueue(
        self,
        operation: str,
        payload: dict[str, Any],
        idempotency_key: str,
        request_hash: str,
        *,
        max_attempts: int = 3,
        available_at: float | None = None,
    ) -> QueueJob: ...

    def lease(
        self, worker_id: str, *, lease_seconds: float = 30, now: float | None = None
    ) -> QueueJob | None: ...

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: float = 30,
        now: float | None = None,
    ) -> QueueJob: ...

    def complete(self, job_id: str, worker_id: str) -> QueueJob: ...

    def fail(
        self,
        job_id: str,
        worker_id: str,
        error_code: str,
        *,
        retryable: bool,
        retry_delay: float = 0,
        now: float | None = None,
    ) -> QueueJob: ...

    def request_cancel(self, job_id: str) -> QueueJob: ...

    def retry(self, job_id: str) -> QueueJob: ...

    def recover_expired(self, *, now: float | None = None) -> int: ...

    def get(self, job_id: str) -> QueueJob | None: ...
