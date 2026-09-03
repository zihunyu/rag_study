"""Redis-backed shared job queue with lease and idempotency semantics."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any, cast

from ragkb.adapters.redis_cache import RedisCacheRateLimitAdapter
from ragkb.contracts.jobs import QueueConflictError, QueueJob, QueueLeaseError, QueueStateError
from ragkb.domain.ids import new_uuid7
from ragkb.domain.state_machines import JobState


class RedisPersistentJobQueue:
    revision = "redis-persistent-queue:g4-v1"

    def __init__(self, redis: RedisCacheRateLimitAdapter) -> None:
        self.redis = redis
        self.jobs_key = redis._key("queue", "jobs")
        self.idempotency_key = redis._key("queue", "idempotency")
        self.lock_key = redis._key("queue", "mutation-lock")

    @property
    def client(self) -> Any:
        return self.redis._connected()

    def _lock(self) -> AbstractContextManager[Any]:
        return cast(
            AbstractContextManager[Any],
            self.client.lock(self.lock_key, timeout=30, blocking_timeout=30),
        )

    @staticmethod
    def _job(data: Mapping[str, Any]) -> QueueJob:
        return QueueJob(
            id=str(data["id"]),
            operation=str(data["operation"]),
            payload=dict(data["payload"]),
            idempotency_key=str(data["idempotency_key"]),
            request_hash=str(data["request_hash"]),
            state=JobState(str(data["state"])),
            attempt=int(data["attempt"]),
            max_attempts=int(data["max_attempts"]),
            lease_owner=str(data["lease_owner"]) if data.get("lease_owner") else None,
            lease_expires_at=(
                float(data["lease_expires_at"])
                if data.get("lease_expires_at") is not None
                else None
            ),
            heartbeat_at=(
                float(data["heartbeat_at"]) if data.get("heartbeat_at") is not None else None
            ),
            next_retry_at=(
                float(data["next_retry_at"]) if data.get("next_retry_at") is not None else None
            ),
            cancel_requested=bool(data.get("cancel_requested")),
            error_code=str(data["error_code"]) if data.get("error_code") else None,
        )

    def _load_record(self, job_id: str) -> dict[str, Any] | None:
        raw = self.client.hget(self.jobs_key, job_id)
        if raw is None:
            return None
        loaded = json.loads(str(raw))
        if not isinstance(loaded, dict):
            raise QueueStateError("REDIS_QUEUE_RECORD_INVALID")
        return loaded

    def _required(self, job_id: str) -> dict[str, Any]:
        record = self._load_record(job_id)
        if record is None:
            raise KeyError(job_id)
        return record

    def _save(self, record: Mapping[str, Any]) -> QueueJob:
        encoded = json.dumps(
            dict(record), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        self.client.hset(self.jobs_key, str(record["id"]), encoded)
        return self._job(record)

    def _records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for raw in self.client.hgetall(self.jobs_key).values():
            loaded = json.loads(str(raw))
            if isinstance(loaded, dict):
                records.append(loaded)
        return records

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
        if not operation or not idempotency_key or not request_hash or max_attempts < 1:
            raise ValueError("queue enqueue parameters are invalid")
        identity = f"{operation}:{idempotency_key}"
        with self._lock():
            existing_id = self.client.hget(self.idempotency_key, identity)
            if existing_id is not None:
                existing = self._required(str(existing_id))
                if str(existing["request_hash"]) != request_hash:
                    raise QueueConflictError("idempotency key reused with a different request hash")
                return self._job(existing)
            now = time.time()
            record = {
                "id": new_uuid7(),
                "operation": operation,
                "payload": payload,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "state": JobState.QUEUED.value,
                "attempt": 0,
                "max_attempts": max_attempts,
                "available_at": now if available_at is None else available_at,
                "lease_owner": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "next_retry_at": None,
                "cancel_requested": False,
                "error_code": None,
                "created_at": now,
                "updated_at": now,
            }
            self.client.hset(self.idempotency_key, identity, record["id"])
            return self._save(record)

    @staticmethod
    def _recover_record(record: dict[str, Any], now: float) -> bool:
        if (
            record["state"]
            not in {
                JobState.RUNNING.value,
                JobState.CANCEL_REQUESTED.value,
            }
            or record.get("lease_expires_at") is None
        ):
            return False
        if float(record["lease_expires_at"]) > now:
            return False
        if record.get("cancel_requested"):
            record["state"] = JobState.CANCELLED.value
            record["next_retry_at"] = None
        elif int(record["attempt"]) < int(record["max_attempts"]):
            record["state"] = JobState.RETRY_WAIT.value
            record["next_retry_at"] = now
        else:
            record["state"] = JobState.FAILED_FINAL.value
            record["next_retry_at"] = None
        record.update(lease_owner=None, lease_expires_at=None, heartbeat_at=None, updated_at=now)
        return True

    def recover_expired(self, *, now: float | None = None) -> int:
        timestamp = time.time() if now is None else now
        recovered = 0
        with self._lock():
            for record in self._records():
                if self._recover_record(record, timestamp):
                    self._save(record)
                    recovered += 1
        return recovered

    def lease(
        self, worker_id: str, *, lease_seconds: float = 30, now: float | None = None
    ) -> QueueJob | None:
        if not worker_id or lease_seconds <= 0:
            raise ValueError("worker_id and lease_seconds are required")
        timestamp = time.time() if now is None else now
        with self._lock():
            records = self._records()
            for record in records:
                if self._recover_record(record, timestamp):
                    self._save(record)
                if (
                    record["state"] == JobState.RETRY_WAIT.value
                    and record.get("next_retry_at") is not None
                    and float(record["next_retry_at"]) <= timestamp
                ):
                    record["state"] = JobState.QUEUED.value
                    record["updated_at"] = timestamp
                    self._save(record)
            eligible = [
                record
                for record in records
                if record["state"] == JobState.QUEUED.value
                and float(record["available_at"]) <= timestamp
                and not record.get("cancel_requested")
            ]
            if not eligible:
                return None
            record = min(eligible, key=lambda item: (float(item["created_at"]), str(item["id"])))
            record.update(
                state=JobState.RUNNING.value,
                attempt=int(record["attempt"]) + 1,
                lease_owner=worker_id,
                lease_expires_at=timestamp + lease_seconds,
                heartbeat_at=timestamp,
                updated_at=timestamp,
            )
            return self._save(record)

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: float = 30,
        now: float | None = None,
    ) -> QueueJob:
        timestamp = time.time() if now is None else now
        with self._lock():
            record = self._required(job_id)
            if (
                record["state"]
                not in {
                    JobState.RUNNING.value,
                    JobState.CANCEL_REQUESTED.value,
                }
                or record.get("lease_owner") != worker_id
            ):
                raise QueueLeaseError("job is not leased by this worker")
            if not record.get("cancel_requested"):
                record.update(
                    heartbeat_at=timestamp,
                    lease_expires_at=timestamp + lease_seconds,
                    updated_at=timestamp,
                )
                return self._save(record)
            return self._job(record)

    def complete(self, job_id: str, worker_id: str) -> QueueJob:
        with self._lock():
            record = self._required(job_id)
            if record.get("lease_owner") != worker_id or record["state"] not in {
                JobState.RUNNING.value,
                JobState.CANCEL_REQUESTED.value,
            }:
                raise QueueLeaseError("job is not leased by this worker")
            record.update(
                state=(
                    JobState.CANCELLED.value
                    if record.get("cancel_requested")
                    else JobState.SUCCEEDED.value
                ),
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                updated_at=time.time(),
            )
            return self._save(record)

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
        timestamp = time.time() if now is None else now
        with self._lock():
            record = self._required(job_id)
            if record.get("lease_owner") != worker_id or record["state"] not in {
                JobState.RUNNING.value,
                JobState.CANCEL_REQUESTED.value,
            }:
                raise QueueLeaseError("job is not leased by this worker")
            if record.get("cancel_requested"):
                state, next_retry = JobState.CANCELLED, None
            elif retryable and int(record["attempt"]) < int(record["max_attempts"]):
                state, next_retry = JobState.RETRY_WAIT, timestamp + max(0, retry_delay)
            else:
                state, next_retry = JobState.FAILED_FINAL, None
            record.update(
                state=state.value,
                error_code=error_code,
                next_retry_at=next_retry,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                updated_at=timestamp,
            )
            return self._save(record)

    def request_cancel(self, job_id: str) -> QueueJob:
        with self._lock():
            record = self._required(job_id)
            if record["state"] in {JobState.QUEUED.value, JobState.RETRY_WAIT.value}:
                record["state"] = JobState.CANCELLED.value
            elif record["state"] == JobState.RUNNING.value:
                record["state"] = JobState.CANCEL_REQUESTED.value
            else:
                return self._job(record)
            record["cancel_requested"] = True
            record["updated_at"] = time.time()
            return self._save(record)

    def acknowledge_cancel(self, job_id: str, worker_id: str) -> QueueJob:
        with self._lock():
            record = self._required(job_id)
            if (
                record["state"] != JobState.CANCEL_REQUESTED.value
                or not record.get("cancel_requested")
                or record.get("lease_owner") != worker_id
            ):
                raise QueueLeaseError("cancel request is not leased by this worker")
            record.update(
                state=JobState.CANCELLED.value,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                next_retry_at=None,
                updated_at=time.time(),
            )
            return self._save(record)

    def retry(self, job_id: str) -> QueueJob:
        with self._lock():
            record = self._required(job_id)
            if record["state"] not in {
                JobState.FAILED_FINAL.value,
                JobState.CANCELLED.value,
            }:
                raise QueueStateError("only failed or cancelled jobs can be retried")
            timestamp = time.time()
            record.update(
                state=JobState.QUEUED.value,
                attempt=0,
                cancel_requested=False,
                error_code=None,
                next_retry_at=None,
                available_at=timestamp,
                updated_at=timestamp,
            )
            return self._save(record)

    def get(self, job_id: str) -> QueueJob | None:
        record = self._load_record(job_id)
        return self._job(record) if record is not None else None
