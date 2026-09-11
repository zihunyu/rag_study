"""Task-local accounting, propagated with contextvars into retrieval threads."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

logger = logging.getLogger(__name__)


class ReuseLedgerPort(Protocol):
    def begin(self, task_id: str, attempt_id: str, metadata: dict[str, Any]) -> None: ...
    def event(self, attempt_id: str, event_id: str, kind: str, data: dict[str, Any]) -> None: ...
    def finish(self, attempt_id: str, state: str) -> None: ...
    def report(self, task_id: str) -> dict[str, Any]: ...
    def identity(self, task_id: str) -> dict[str, Any] | None: ...


@dataclass
class TaskUsage:
    ledger: ReuseLedgerPort
    task_id: str
    attempt_id: str
    state: str = "completed"
    unavailable: bool = False
    begun: bool = False

    def failed(self) -> None:
        if not self.unavailable:
            logger.warning("TASK_REUSE_STATISTICS_UNAVAILABLE")
        self.unavailable = True

    def record(self, kind: str, data: dict[str, Any], event_id: str | None = None) -> str:
        key = event_id or uuid4().hex
        if not self.unavailable:
            try:
                self.ledger.event(self.attempt_id, key, kind, data)
            except Exception:
                # Observability must never discard an already paid model response.
                # Stop this attempt's writes rather than silently reporting partial totals.
                self.failed()
        return key

    def report(self) -> dict[str, Any]:
        if not self.unavailable:
            try:
                return self.ledger.report(self.task_id)
            except Exception:
                self.failed()
        return {
            "available": False,
            "complete": False,
            "task_id": self.task_id,
            "reason": "TASK_REUSE_STATISTICS_UNAVAILABLE",
        }

    def finish(self) -> None:
        if not self.begun:
            return
        try:
            if self.unavailable:
                self.ledger.event(self.attempt_id, uuid4().hex, "statistics_unavailable", {})
            self.ledger.finish(self.attempt_id, self.state)
        except Exception:
            self.failed()


current_usage: ContextVar[TaskUsage | None] = ContextVar("reuse_task_usage", default=None)


def record(kind: str, **values: int) -> None:
    active = current_usage.get()
    if active:
        active.record(kind, values)


@contextmanager
def task_usage(
    ledger: ReuseLedgerPort | None, task_id: str, *, reuse_existing: bool = False, **metadata: Any
) -> Iterator[TaskUsage | None]:
    if ledger is None:
        yield None
        return
    existing = current_usage.get()
    if reuse_existing and existing and existing.ledger is ledger:
        try:
            identity = ledger.identity(existing.task_id) or {}
        except Exception:
            existing.failed()
            identity = {}
        if all(
            identity.get(key, "") == metadata.get(key, "")
            for key in (
                "kind",
                "tenant_id",
                "user_id",
                "space_id",
                "document_id",
                "document_version_id",
            )
        ):
            yield existing
            return
    attempt = TaskUsage(ledger, task_id, uuid4().hex)
    try:
        ledger.begin(task_id, attempt.attempt_id, metadata)
        attempt.begun = True
    except ValueError:
        # Identity violations are authorization/programming errors, not telemetry outages.
        raise
    except Exception:
        attempt.failed()
    token = current_usage.set(attempt)
    try:
        yield attempt
    except BaseException:
        attempt.state = "failed"
        raise
    finally:
        current_usage.reset(token)
        attempt.finish()
