from __future__ import annotations

from pathlib import Path

import pytest
from ragkb.contracts.jobs import QueueConflictError, QueueLeaseError
from ragkb.domain.state_machines import JobState
from ragkb.infrastructure.sqlite import SQLiteDatabase
from ragkb.infrastructure.sqlite_queue import SQLitePersistentJobQueue


def _queue(tmp_path: Path) -> SQLitePersistentJobQueue:
    return SQLitePersistentJobQueue(SQLiteDatabase(tmp_path / "queue.sqlite3"))


def test_enqueue_is_idempotent_and_detects_hash_conflict(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    first = queue.enqueue("parse", {"version_id": "v1"}, "key-1", "hash-1")
    replay = queue.enqueue("parse", {"version_id": "v1"}, "key-1", "hash-1")

    assert replay.id == first.id
    with pytest.raises(QueueConflictError):
        queue.enqueue("parse", {"version_id": "v2"}, "key-1", "hash-2")


def test_lease_heartbeat_and_complete(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    queued = queue.enqueue("parse", {}, "key", "hash", available_at=100)
    leased = queue.lease("worker-a", now=100, lease_seconds=10)

    assert leased is not None
    assert leased.id == queued.id
    assert leased.state is JobState.RUNNING
    assert leased.attempt == 1
    heartbeat = queue.heartbeat(leased.id, "worker-a", now=105, lease_seconds=10)
    assert heartbeat.lease_expires_at == 115
    completed = queue.complete(leased.id, "worker-a")
    assert completed.state is JobState.SUCCEEDED


def test_expired_lease_recovers_after_process_restart_at_least_once(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "queue.sqlite3")
    first_process = SQLitePersistentJobQueue(database)
    job = first_process.enqueue("parse", {"value": 1}, "key", "hash", available_at=100)
    leased = first_process.lease("worker-a", now=100, lease_seconds=10)
    assert leased is not None

    restarted_process = SQLitePersistentJobQueue(database)
    assert restarted_process.recover_expired(now=111) == 1
    redelivered = restarted_process.lease("worker-b", now=111, lease_seconds=10)

    assert redelivered is not None
    assert redelivered.id == job.id
    assert redelivered.attempt == 2
    assert redelivered.lease_owner == "worker-b"


def test_retry_and_final_failure_semantics(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    job = queue.enqueue("parse", {}, "key", "hash", max_attempts=2, available_at=10)
    leased = queue.lease("worker", now=10)
    assert leased is not None
    waiting = queue.fail(job.id, "worker", "TEMPORARY", retryable=True, retry_delay=5, now=10)
    assert waiting.state is JobState.RETRY_WAIT
    assert queue.lease("worker", now=14) is None
    second = queue.lease("worker", now=15)
    assert second is not None
    final = queue.fail(second.id, "worker", "TEMPORARY", retryable=True, now=15)
    assert final.state is JobState.FAILED_FINAL
    retried = queue.retry(final.id)
    assert retried.state is JobState.QUEUED
    assert retried.attempt == 0


def test_cancel_queued_and_running_jobs(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    queued = queue.enqueue("parse", {}, "queued", "hash-1")
    assert queue.request_cancel(queued.id).state is JobState.CANCELLED

    running = queue.enqueue("parse", {}, "running", "hash-2", available_at=20)
    lease = queue.lease("worker", now=20)
    assert lease is not None and lease.id == running.id
    requested = queue.request_cancel(running.id)
    assert requested.state is JobState.CANCEL_REQUESTED
    assert queue.complete(running.id, "worker").state is JobState.CANCELLED


def test_wrong_worker_cannot_mutate_lease(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    queue.enqueue("parse", {}, "key", "hash", available_at=1)
    job = queue.lease("worker-a", now=1)
    assert job is not None

    with pytest.raises(QueueLeaseError):
        queue.heartbeat(job.id, "worker-b", now=2)
