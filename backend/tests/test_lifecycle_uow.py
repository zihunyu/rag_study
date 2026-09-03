from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from ragkb.application.lifecycle import LifecycleService
from ragkb.domain.lifecycle import LifecycleState
from ragkb.infrastructure.lifecycle_repository import SQLiteLifecycleStore
from ragkb.infrastructure.sqlite import SQLiteDatabase


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
