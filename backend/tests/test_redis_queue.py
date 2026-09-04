from __future__ import annotations

from contextlib import contextmanager

import pytest
from ragkb.adapters.redis_queue import RedisPersistentJobQueue
from ragkb.contracts.jobs import QueueConflictError
from ragkb.domain.state_machines import JobState


class _Client:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    @contextmanager
    def lock(self, name, **kwargs):
        del name, kwargs
        yield

    def hget(self, name, key):
        return self.hashes.get(name, {}).get(str(key))

    def hset(self, name, key, value):
        self.hashes.setdefault(name, {})[str(key)] = str(value)

    def hgetall(self, name):
        return dict(self.hashes.get(name, {}))

    def hdel(self, name, key):
        return int(self.hashes.get(name, {}).pop(str(key), None) is not None)


class _Redis:
    def __init__(self) -> None:
        self.client = _Client()

    def _connected(self):
        return self.client

    def _key(self, namespace, key):
        return f"test:{namespace}:{key}"


def test_redis_queue_is_shared_idempotent_and_lease_safe() -> None:
    redis = _Redis()
    first = RedisPersistentJobQueue(redis)  # type: ignore[arg-type]
    second = RedisPersistentJobQueue(redis)  # type: ignore[arg-type]

    queued = first.enqueue("process", {"id": 1}, "same", "hash", available_at=100)
    assert second.enqueue("process", {"id": 1}, "same", "hash").id == queued.id
    with pytest.raises(QueueConflictError):
        second.enqueue("process", {"id": 2}, "same", "other")

    leased = second.lease("worker", now=100, lease_seconds=10)
    assert leased is not None and leased.state is JobState.RUNNING
    completed = first.complete(leased.id, "worker")
    assert completed.state is JobState.SUCCEEDED


def test_redis_final_failure_enters_dlq_and_manual_retry_removes_it() -> None:
    queue = RedisPersistentJobQueue(_Redis())  # type: ignore[arg-type]
    queued = queue.enqueue(
        "process", {"document_id": "doc"}, "dlq", "hash", max_attempts=1, available_at=1
    )
    leased = queue.lease("worker", now=1)
    assert leased is not None

    failed = queue.fail(queued.id, "worker", "DEPENDENCY_DOWN", retryable=True, now=2)

    assert failed.state is JobState.FAILED_FINAL
    assert queue.dead_letters()[0]["job_id"] == queued.id
    assert queue.dead_letters()[0]["attempt"] == 1
    assert queue.retry(queued.id).state is JobState.QUEUED
    assert queue.dead_letters() == ()


def test_redis_fresh_job_is_not_starved_by_due_retry() -> None:
    queue = RedisPersistentJobQueue(_Redis())  # type: ignore[arg-type]
    retry = queue.enqueue("process", {}, "retry", "hash-1", available_at=1)
    leased = queue.lease("worker", now=1)
    assert leased is not None
    queue.fail(retry.id, "worker", "TEMPORARY", retryable=True, retry_delay=1, now=1)
    fresh = queue.enqueue("process", {}, "fresh", "hash-2", available_at=2)

    selected = queue.lease("worker", now=2)

    assert selected is not None and selected.id == fresh.id
