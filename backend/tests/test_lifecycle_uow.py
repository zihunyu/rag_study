from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from ragkb.application.lifecycle import InMemoryLifecycleStore, LifecycleService
from ragkb.domain.lifecycle import LifecycleState
from ragkb.infrastructure.lifecycle_repository import SQLiteLifecycleStore
from ragkb.infrastructure.sqlite import SQLiteDatabase


class _DurableIntentStore(InMemoryLifecycleStore):
    durable_publication_intents = True

    def __init__(self) -> None:
        super().__init__()
        self.persisted: list[tuple[LifecycleState, bool]] = []
        self.projection_failures: list[tuple[str, str, str]] = []

    def persist_state(self, **kwargs) -> None:
        super().persist_state(**kwargs)
        record = next(iter(self.documents.values()))
        self.persisted.append((record.lifecycle_state, record.visible))

    def mark_publication_projection_failed(self, operation: str, key: str, error_code: str) -> None:
        self.projection_failures.append((operation, key, error_code))


class _Projection:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def set_document_projection(self, *args, **kwargs) -> None:
        del args, kwargs
        if self.fail:
            raise RuntimeError("projection unavailable")

    def delete_document_projection(self, document_id: str) -> None:
        del document_id

    def delete_version_projection(self, document_id: str, version_id: str) -> None:
        del document_id, version_id

    def set_version_security_projection(self, *args, **kwargs) -> None:
        del args, kwargs


@pytest.mark.parametrize("operation", ["publish", "acl", "delete"])
def test_sqlite_commit_failure_rolls_back_memory_audit_outbox_and_idempotency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    database = SQLiteDatabase(tmp_path / f"{operation}.sqlite3")
    store = SQLiteLifecycleStore(database)
    service = LifecycleService(store, "tenant")
    service.register_document("doc", "v1", trace_id="register")
    if operation == "publish":
        service.revoke("doc", event_id="baseline-revoke", trace_id="baseline")
    elif operation == "acl":
        service.publish("doc", "v1", event_id="baseline-publish", trace_id="baseline")
    audit_before = tuple(store.audit_events)

    @contextmanager
    def _failed_transaction(*, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        del immediate
        raise sqlite3.OperationalError("injected sqlite commit failure")
        yield  # pragma: no cover

    monkeypatch.setattr(database, "transaction", _failed_transaction)

    def action() -> object:
        if operation == "publish":
            return service.publish("doc", "v2", event_id="failed", trace_id="failure")
        if operation == "acl":
            return service.update_acl(
                "doc",
                2,
                1,
                1,
                projection_ok=True,
                event_id="failed",
                trace_id="failure",
            )
        return service.delete("doc", event_id="failed", trace_id="failure")

    with pytest.raises(sqlite3.OperationalError, match="injected"):
        action()

    disk = SQLiteLifecycleStore(database)
    assert store.documents["doc"] == disk.documents["doc"]
    assert tuple(store.audit_events) == audit_before == tuple(disk.audit_events)
    idempotency_operation = "acl-update:doc" if operation == "acl" else f"{operation}:doc"
    assert store.get_idempotency("tenant", idempotency_operation, "failed") is None
    assert "doc" not in store.tombstones
    assert "doc" not in disk.tombstones
    if operation == "publish":
        assert store.documents["doc"].lifecycle_state is LifecycleState.REVOKED
        assert store.is_accessible("doc") is False


def test_durable_publication_persists_switching_before_projection_and_active_after() -> None:
    store = _DurableIntentStore()
    service = LifecycleService(store, "tenant", retrieval_projection=_Projection())
    service.register_document("doc", "v1", trace_id="register")
    store.persisted.clear()

    result = service.publish("doc", "v1", event_id="publish", trace_id="trace")

    assert store.persisted == [
        (LifecycleState.SWITCHING, False),
        (LifecycleState.ACTIVE, True),
    ]
    assert result.lifecycle_state is LifecycleState.ACTIVE


def test_durable_publication_failure_remains_fail_closed_and_records_outbox_failure() -> None:
    store = _DurableIntentStore()
    service = LifecycleService(store, "tenant", retrieval_projection=_Projection(fail=True))
    service.register_document("doc", "v1", trace_id="register")
    store.persisted.clear()

    with pytest.raises(RuntimeError, match="projection unavailable"):
        service.publish("doc", "v1", event_id="publish", trace_id="trace")

    assert store.persisted == [(LifecycleState.SWITCHING, False)]
    assert store.projection_failures == [("publish:doc", "publish", "RuntimeError")]
    assert store.is_accessible("doc") is False
