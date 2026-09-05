"""Transaction fencing for ingestion attempts; the counter never resets on retry."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from ragkb.contracts.jobs import QueueJob, QueueLeaseError


@dataclass(frozen=True)
class IngestionFence:
    tenant_id: str
    version_id: str
    job_id: str
    token: int


current_fence: ContextVar[IngestionFence | None] = ContextVar("ingestion_fence", default=None)


@contextmanager
def sqlite_ingestion_scope(job: QueueJob) -> Iterator[None]:
    handle = current_fence.set(
        IngestionFence(
            str(job.payload["tenant_id"]),
            str(job.payload["document_version_id"]),
            job.id,
            job.fence_token,
        )
    )
    try:
        yield
    finally:
        current_fence.reset(handle)


def check_sqlite_fence(connection: Any) -> None:
    fence = current_fence.get()
    if fence is None:
        return
    row = connection.execute(
        "SELECT fence_token FROM job_queue WHERE id=?", (fence.job_id,)
    ).fetchone()
    if row is None or int(row[0]) != fence.token or fence.token < 1:
        raise QueueLeaseError("INGEST_FENCE_STALE")


def check_mysql_fence(connection: Any) -> None:
    fence = current_fence.get()
    if fence is None:
        return
    cursor = connection.cursor()
    cursor.execute(
        "SELECT job_id, fence_token FROM ingestion_fences "
        "WHERE tenant_id=%s AND version_id=%s FOR UPDATE",
        (fence.tenant_id, fence.version_id),
    )
    row = cursor.fetchone()
    if isinstance(row, dict):
        row = (row["job_id"], row["fence_token"])
    if row is None or (str(row[0]), int(row[1])) != (fence.job_id, fence.token):
        raise QueueLeaseError("INGEST_FENCE_STALE")


@contextmanager
def mysql_ingestion_scope(control: Any, job: QueueJob) -> Iterator[None]:
    fence = IngestionFence(
        str(job.payload["tenant_id"]),
        str(job.payload["document_version_id"]),
        job.id,
        job.fence_token,
    )
    if fence.token < 1:
        raise QueueLeaseError("INGEST_FENCE_REQUIRED")
    connection = control.connect()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT IGNORE INTO ingestion_fences(tenant_id, version_id, job_id, fence_token) "
            "VALUES (%s, %s, %s, 0)",
            (fence.tenant_id, fence.version_id, fence.job_id),
        )
        cursor.execute(
            "UPDATE ingestion_fences SET fence_token=%s "
            "WHERE tenant_id=%s AND version_id=%s AND job_id=%s AND fence_token < %s",
            (fence.token, fence.tenant_id, fence.version_id, fence.job_id, fence.token),
        )
        if cursor.rowcount != 1:
            raise QueueLeaseError("INGEST_FENCE_STALE")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    handle = current_fence.set(fence)
    try:
        yield
    finally:
        current_fence.reset(handle)
