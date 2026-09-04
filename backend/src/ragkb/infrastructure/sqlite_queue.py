"""SQLite-backed local persistent queue with at-least-once leasing semantics."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from ragkb.contracts.jobs import QueueConflictError, QueueJob, QueueLeaseError, QueueStateError
from ragkb.domain.ids import new_uuid7
from ragkb.domain.state_machines import JobState
from ragkb.infrastructure.sqlite import SQLiteDatabase


class SQLitePersistentJobQueue:
    revision = "sqlite-persistent-queue:dlq:g1-v2"

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.database.initialize()

    @staticmethod
    def _timestamp(now: float | None) -> float:
        return time.time() if now is None else now

    @staticmethod
    def _from_row(row: sqlite3.Row) -> QueueJob:
        return QueueJob(
            id=str(row["id"]),
            operation=str(row["operation"]),
            payload=json.loads(str(row["payload_json"])),
            idempotency_key=str(row["idempotency_key"]),
            request_hash=str(row["request_hash"]),
            state=JobState(str(row["state"])),
            attempt=int(row["attempt"]),
            max_attempts=int(row["max_attempts"]),
            lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
            lease_expires_at=(
                float(row["lease_expires_at"]) if row["lease_expires_at"] is not None else None
            ),
            heartbeat_at=float(row["heartbeat_at"]) if row["heartbeat_at"] is not None else None,
            next_retry_at=(
                float(row["next_retry_at"]) if row["next_retry_at"] is not None else None
            ),
            cancel_requested=bool(row["cancel_requested"]),
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        )

    def _get_in(self, connection: sqlite3.Connection, job_id: str) -> QueueJob:
        row = connection.execute("SELECT * FROM job_queue WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._from_row(row)

    @staticmethod
    def _dead_letter_in(
        connection: sqlite3.Connection,
        job_id: str,
        error_code: str,
        attempt: int,
        payload_json: str,
        failed_at: float,
    ) -> None:
        connection.execute(
            """
            INSERT INTO job_dead_letters(job_id, error_code, attempt, payload_json, failed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                error_code=excluded.error_code,
                attempt=excluded.attempt,
                payload_json=excluded.payload_json,
                failed_at=excluded.failed_at
            """,
            (job_id, error_code, attempt, payload_json, failed_at),
        )

    def enqueue(
        self,
        operation: str,
        payload: dict[str, Any],
        idempotency_key: str,
        request_hash: str,
        *,
        max_attempts: int = 3,
        available_at: float | None = None,
    ) -> QueueJob:
        if not operation or not idempotency_key or not request_hash:
            raise ValueError("operation, idempotency_key and request_hash are required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        now = time.time()
        ready_at = now if available_at is None else available_at
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM job_queue WHERE operation = ? AND idempotency_key = ?",
                (operation, idempotency_key),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise QueueConflictError("idempotency key reused with a different request hash")
                return self._from_row(existing)
            job_id = new_uuid7()
            connection.execute(
                """
                INSERT INTO job_queue(
                    id, operation, payload_json, idempotency_key, request_hash, state,
                    attempt, max_attempts, available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    operation,
                    payload_json,
                    idempotency_key,
                    request_hash,
                    JobState.QUEUED.value,
                    max_attempts,
                    ready_at,
                    now,
                    now,
                ),
            )
            return self._get_in(connection, job_id)

    def _recover_expired_in(self, connection: sqlite3.Connection, now: float) -> int:
        rows = connection.execute(
            """
            SELECT * FROM job_queue
            WHERE state IN (?, ?) AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
            """,
            (JobState.RUNNING.value, JobState.CANCEL_REQUESTED.value, now),
        ).fetchall()
        for row in rows:
            if bool(row["cancel_requested"]) or row["state"] == JobState.CANCEL_REQUESTED.value:
                target = JobState.CANCELLED
                next_retry = None
            elif int(row["attempt"]) < int(row["max_attempts"]):
                target = JobState.RETRY_WAIT
                next_retry = now
            else:
                target = JobState.FAILED_FINAL
                next_retry = None
            connection.execute(
                """
                UPDATE job_queue SET state = ?, lease_owner = NULL, lease_expires_at = NULL,
                    heartbeat_at = NULL, next_retry_at = ?, updated_at = ? WHERE id = ?
                """,
                (target.value, next_retry, now, row["id"]),
            )
            if target is JobState.FAILED_FINAL:
                self._dead_letter_in(
                    connection,
                    str(row["id"]),
                    str(row["error_code"] or "LEASE_ATTEMPTS_EXHAUSTED"),
                    int(row["attempt"]),
                    str(row["payload_json"]),
                    now,
                )
        return len(rows)

    def recover_expired(self, *, now: float | None = None) -> int:
        timestamp = self._timestamp(now)
        with self.database.transaction(immediate=True) as connection:
            return self._recover_expired_in(connection, timestamp)

    def lease(
        self, worker_id: str, *, lease_seconds: float = 30, now: float | None = None
    ) -> QueueJob | None:
        if not worker_id or lease_seconds <= 0:
            raise ValueError("worker_id and a positive lease_seconds are required")
        timestamp = self._timestamp(now)
        with self.database.transaction(immediate=True) as connection:
            self._recover_expired_in(connection, timestamp)
            connection.execute(
                """
                UPDATE job_queue SET state = ?, updated_at = ?
                WHERE state = ? AND next_retry_at IS NOT NULL AND next_retry_at <= ?
                """,
                (JobState.QUEUED.value, timestamp, JobState.RETRY_WAIT.value, timestamp),
            )
            row = connection.execute(
                """
                SELECT * FROM job_queue
                WHERE state = ? AND available_at <= ? AND cancel_requested = 0
                ORDER BY (attempt > 0), available_at, created_at, id LIMIT 1
                """,
                (JobState.QUEUED.value, timestamp),
            ).fetchone()
            if row is None:
                return None
            expires = timestamp + lease_seconds
            connection.execute(
                """
                UPDATE job_queue SET state = ?, attempt = attempt + 1, lease_owner = ?,
                    lease_expires_at = ?, heartbeat_at = ?, updated_at = ? WHERE id = ?
                """,
                (
                    JobState.RUNNING.value,
                    worker_id,
                    expires,
                    timestamp,
                    timestamp,
                    row["id"],
                ),
            )
            return self._get_in(connection, str(row["id"]))

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: float = 30,
        now: float | None = None,
    ) -> QueueJob:
        timestamp = self._timestamp(now)
        with self.database.transaction(immediate=True) as connection:
            job = self._get_in(connection, job_id)
            if job.state not in {JobState.RUNNING, JobState.CANCEL_REQUESTED} or (
                job.lease_owner != worker_id
            ):
                raise QueueLeaseError("job is not leased by this worker")
            if job.state is JobState.CANCEL_REQUESTED or job.cancel_requested:
                return job
            connection.execute(
                """
                UPDATE job_queue SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (timestamp, timestamp + lease_seconds, timestamp, job_id),
            )
            return self._get_in(connection, job_id)

    def complete(self, job_id: str, worker_id: str) -> QueueJob:
        with self.database.transaction(immediate=True) as connection:
            job = self._get_in(connection, job_id)
            if job.lease_owner != worker_id or job.state not in {
                JobState.RUNNING,
                JobState.CANCEL_REQUESTED,
            }:
                raise QueueLeaseError("job is not leased by this worker")
            state = JobState.CANCELLED if job.cancel_requested else JobState.SUCCEEDED
            connection.execute(
                """
                UPDATE job_queue SET state = ?, lease_owner = NULL, lease_expires_at = NULL,
                    heartbeat_at = NULL, updated_at = ? WHERE id = ?
                """,
                (state.value, time.time(), job_id),
            )
            return self._get_in(connection, job_id)

    def fail(
        self,
        job_id: str,
        worker_id: str,
        error_code: str,
        *,
        retryable: bool,
        retry_delay: float = 0,
        now: float | None = None,
    ) -> QueueJob:
        timestamp = self._timestamp(now)
        with self.database.transaction(immediate=True) as connection:
            job = self._get_in(connection, job_id)
            if job.lease_owner != worker_id or job.state not in {
                JobState.RUNNING,
                JobState.CANCEL_REQUESTED,
            }:
                raise QueueLeaseError("job is not leased by this worker")
            if job.cancel_requested:
                state, next_retry = JobState.CANCELLED, None
            elif retryable and job.attempt < job.max_attempts:
                state, next_retry = JobState.RETRY_WAIT, timestamp + max(0, retry_delay)
            else:
                state, next_retry = JobState.FAILED_FINAL, None
            connection.execute(
                """
                UPDATE job_queue SET state = ?, error_code = ?, next_retry_at = ?,
                    lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    updated_at = ? WHERE id = ?
                """,
                (state.value, error_code, next_retry, timestamp, job_id),
            )
            if state is JobState.FAILED_FINAL:
                self._dead_letter_in(
                    connection,
                    job.id,
                    error_code,
                    job.attempt,
                    json.dumps(job.payload, sort_keys=True, separators=(",", ":")),
                    timestamp,
                )
            return self._get_in(connection, job_id)

    def request_cancel(self, job_id: str) -> QueueJob:
        with self.database.transaction(immediate=True) as connection:
            job = self._get_in(connection, job_id)
            if job.state in {JobState.QUEUED, JobState.RETRY_WAIT}:
                state = JobState.CANCELLED
            elif job.state is JobState.RUNNING:
                state = JobState.CANCEL_REQUESTED
            else:
                return job
            connection.execute(
                """
                UPDATE job_queue SET state = ?, cancel_requested = 1, updated_at = ?
                WHERE id = ?
                """,
                (state.value, time.time(), job_id),
            )
            return self._get_in(connection, job_id)

    def acknowledge_cancel(self, job_id: str, worker_id: str) -> QueueJob:
        with self.database.transaction(immediate=True) as connection:
            job = self._get_in(connection, job_id)
            if (
                job.state is not JobState.CANCEL_REQUESTED
                or not job.cancel_requested
                or job.lease_owner != worker_id
            ):
                raise QueueLeaseError("cancel request is not leased by this worker")
            connection.execute(
                """
                UPDATE job_queue SET state = ?, lease_owner = NULL, lease_expires_at = NULL,
                    heartbeat_at = NULL, next_retry_at = NULL, updated_at = ? WHERE id = ?
                """,
                (JobState.CANCELLED.value, time.time(), job_id),
            )
            return self._get_in(connection, job_id)

    def retry(self, job_id: str) -> QueueJob:
        with self.database.transaction(immediate=True) as connection:
            job = self._get_in(connection, job_id)
            if job.state not in {JobState.FAILED_FINAL, JobState.CANCELLED}:
                raise QueueStateError("only FAILED_FINAL or CANCELLED jobs can be retried manually")
            timestamp = time.time()
            connection.execute(
                """
                UPDATE job_queue SET state = ?, attempt = 0, cancel_requested = 0,
                    error_code = NULL, next_retry_at = NULL, available_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (JobState.QUEUED.value, timestamp, timestamp, job_id),
            )
            connection.execute("DELETE FROM job_dead_letters WHERE job_id = ?", (job_id,))
            return self._get_in(connection, job_id)

    def get(self, job_id: str) -> QueueJob | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM job_queue WHERE id = ?", (job_id,)).fetchone()
            return self._from_row(row) if row is not None else None

    def dead_letters(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        if limit < 1:
            raise ValueError("dead-letter limit must be positive")
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT job_id, error_code, attempt, payload_json, failed_at
                FROM job_dead_letters ORDER BY failed_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(
            {
                "job_id": str(row["job_id"]),
                "error_code": str(row["error_code"]),
                "attempt": int(row["attempt"]),
                "payload": json.loads(str(row["payload_json"])),
                "failed_at": float(row["failed_at"]),
            }
            for row in rows
        )
