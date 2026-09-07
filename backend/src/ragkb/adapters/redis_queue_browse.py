"""Bounded queue browsing indexes, maintained in the queue's mutation transaction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ragkb.domain.pagination import PageKey, RepositoryPage
from ragkb.domain.state_machines import JobState


def browse_key(jobs_key: str, tenant: str, space: str = "", state: str = "") -> str:
    scope = hashlib.sha256(json.dumps([tenant, space, state]).encode()).hexdigest()
    return f"{jobs_key}:browse:v1:{scope}"


def member(record: Mapping[str, Any]) -> str:
    return f"{int(float(record['updated_at']) * 1000):016d}:{record['id']}"


def index_keys(jobs_key: str, record: Mapping[str, Any]) -> set[str]:
    payload = record["payload"]
    return {
        browse_key(jobs_key, str(payload.get("tenant_id", "")), space, state)
        for space in ("", str(payload.get("space_id", "")))
        for state in ("", str(record["state"]))
    }


def remove_index(pipe: Any, jobs_key: str, record: Mapping[str, Any]) -> None:
    for key in index_keys(jobs_key, record):
        pipe.zrem(key, member(record))


def update_index(
    pipe: Any, jobs_key: str, record: Mapping[str, Any], previous: Mapping[str, Any] | None
) -> None:
    if previous:
        remove_index(pipe, jobs_key, previous)
    for key in index_keys(jobs_key, record):
        pipe.zadd(key, {member(record): 0})


def ensure_browse_index(queue: Any) -> None:
    marker = queue.jobs_key + ":browse:v1:complete"
    if queue.client.get(marker):
        return
    # Writers hold the same lock. Interrupted backfills can be run again safely.
    with queue._lock():
        if queue.client.get(marker):
            return
        for _, raw in queue.client.hscan_iter(queue.jobs_key, count=128):
            record = json.loads(raw)
            pipe = queue.client.pipeline(transaction=True)
            update_index(pipe, queue.jobs_key, record, record)
            pipe.execute()
        queue.client.set(marker, "1")


def browse_jobs(
    queue: Any,
    tenant: str,
    space: str = "",
    state: str = "",
    *,
    limit: int = 30,
    after: PageKey | None = None,
) -> RepositoryPage:
    ensure_browse_index(queue)
    key = browse_key(queue.jobs_key, tenant, space, state)
    bound = f"({after[0]:016d}:{after[1]}" if after else "+"
    members = queue.client.zrevrangebylex(key, bound, "-", start=0, num=limit + 1)
    items: list[dict[str, Any]] = []
    for indexed in members[:limit]:
        job_id = str(indexed).split(":", 1)[1]
        record = queue._load_record(job_id)
        if record and record["payload"].get("tenant_id") == tenant:
            items.append(record)
    last = str(members[limit - 1]).split(":", 1) if len(members) > limit else None
    return RepositoryPage(items, (int(last[0]), last[1]) if last else None)


def count_jobs(queue: Any, tenant: str, space: str = "") -> dict[str, int]:
    ensure_browse_index(queue)
    return {
        state.value: int(queue.client.zcard(browse_key(queue.jobs_key, tenant, space, state.value)))
        for state in JobState
    }
