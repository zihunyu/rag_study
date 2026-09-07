from __future__ import annotations

from contextlib import contextmanager

import pytest
from ragkb.adapters.redis_queue import RedisPersistentJobQueue
from ragkb.contracts.jobs import QueueConflictError
from ragkb.domain.state_machines import JobState


class _Client:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.sorted_sets = {}
        self.strings = {}
        self.fail_transaction = False

    def pipeline(self, transaction=True):
        assert transaction
        client = self

        class Pipeline:
            def __init__(self):
                self.operations = []

            def __getattr__(self, method):
                def command(*args, **kwargs):
                    self.operations.append((method, args, kwargs))
                    return self

                return command

            def execute(self):
                if client.fail_transaction:
                    raise ConnectionError("transaction disconnected before EXEC")
                return [
                    getattr(client, method)(*args, **kwargs)
                    for method, args, kwargs in self.operations
                ]

        return Pipeline()

    def get(self, key):
        return self.strings.get(key)

    def set(self, key, value):
        self.strings[key] = value

    def hscan_iter(self, name, **kwargs):
        yield from tuple(self.hashes.get(name, {}).items())

    def zadd(self, name, values):
        self.sorted_sets.setdefault(name, {}).update(values)

    def zrem(self, name, key):
        self.sorted_sets.get(name, {}).pop(key, None)

    def zrangebyscore(self, name, minimum, maximum, start=0, num=128):
        rows = sorted(self.sorted_sets.get(name, {}).items(), key=lambda p: (p[1], p[0]))
        return [key for key, value in rows if float(minimum) <= value <= float(maximum)][
            start : start + num
        ]

    def zrevrange(self, name, start, end):
        rows = sorted(self.sorted_sets.get(name, {}).items(), key=lambda p: p[1], reverse=True)
        return [key for key, _ in rows][start : end + 1]

    def zcard(self, name):
        return len(self.sorted_sets.get(name, {}))

    def zrevrangebylex(self, name, maximum, minimum, start=0, num=30):
        assert minimum == "-"
        rows = sorted(self.sorted_sets.get(name, {}), reverse=True)
        return [key for key in rows if maximum == "+" or key < maximum[1:]][start:start + num]

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


def test_queue_browse_backfills_once_filters_before_paging_and_tracks_states(monkeypatch):
    redis = _Redis()
    queue = RedisPersistentJobQueue(redis)  # type: ignore[arg-type]
    jobs = [queue.enqueue("process", {"tenant_id": tenant, "space_id": space},
                          str(i), str(i), available_at=1)
            for i, (tenant, space) in enumerate((("t", "a"), ("t", "b"),
                                                ("other", "a"), ("t", "a")))]
    # Simulate an existing queue from before browsing indexes were deployed.
    for key in list(redis.client.sorted_sets):
        if ":browse:" in key:
            del redis.client.sorted_sets[key]
    page = queue.list_jobs_page("t", "a", limit=1)
    assert len(page.items) == 1 and page.next_key
    other = queue.list_jobs_page("t", "a", limit=1, after=page.next_key)
    assert {page.items[0]["id"], other.items[0]["id"]} == {jobs[0].id, jobs[3].id}
    assert other.next_key is None
    def no_scan(*args, **kwargs):
        raise AssertionError("A completed backfill must not scan the queue again")
    with monkeypatch.context() as context:
        context.setattr(redis.client, "hscan_iter", no_scan)
        assert queue.job_counts("t", "a")["QUEUED"] == 2
        assert queue.list_jobs_page("t", "a").items
    leased = queue.lease("worker", now=1)
    assert leased
    scope = queue._load_record(leased.id)["payload"]
    queue.complete(leased.id, "worker")
    monkeypatch.setattr(redis.client, "hscan_iter", no_scan)
    assert queue.job_counts(scope["tenant_id"], scope["space_id"])["SUCCEEDED"] == 1
    assert queue.list_jobs_page(scope["tenant_id"], scope["space_id"], "SUCCEEDED").items[0]["id"] == leased.id
