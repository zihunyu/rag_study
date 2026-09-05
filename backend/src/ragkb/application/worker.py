"""G1 local Worker that processes persistent queue jobs."""

from __future__ import annotations

import json
import random
import time
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any, Protocol

from ragkb.application.lease_guard import LeaseGuard
from ragkb.application.tracing import InMemoryTracer, TracerPort
from ragkb.contracts.jobs import PersistentJobQueuePort, QueueJob, QueueLeaseError
from ragkb.contracts.ports import ChunkerPort, ContentStoragePort, ParserRouterPort, ParsingDeferred
from ragkb.contracts.uploads import UploadRepositoryPort
from ragkb.domain.errors import (
    ConfigurationError,
    IngestionCancelled,
    InvalidProviderResponse,
    SchemaMismatch,
    TransientProviderError,
)
from ragkb.domain.retrieval import SecurityProjection
from ragkb.domain.state_machines import JobState
from ragkb.domain.validation import DocumentQualityReport


class LocalIndexingSinkPort(Protocol):
    def index(
        self,
        result: Any,
        *,
        document_id: str,
        tenant_id: str,
        space_id: str,
        permission_revision: int = 1,
        security_projection: SecurityProjection | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> bool: ...


@dataclass(frozen=True)
class WorkerFailure:
    job_id: str
    document_id: str
    attempt: int
    error_code: str
    exception_type: str
    trace_id: str
    retryable: bool
    retry_delay_seconds: float
    dependency_failure: bool


class LocalIngestionWorker:
    revision = "local-ingestion-worker:g1-v1"

    def __init__(
        self,
        queue: PersistentJobQueuePort,
        repository: UploadRepositoryPort,
        storage: ContentStoragePort,
        parser_router: ParserRouterPort,
        worker_id: str,
        lease_seconds: float = 600,
        chunker: ChunkerPort | None = None,
        indexing_sink: LocalIndexingSinkPort | None = None,
        tracer: TracerPort | None = None,
        retry_base_seconds: float = 5.0,
        retry_max_seconds: float = 300.0,
        retry_jitter_seconds: float = 1.0,
        transient_max_attempts: int = 5,
        dependency_failure_threshold: int = 3,
        dependency_cooldown_seconds: float = 30.0,
        failure_pause_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if (
            retry_base_seconds <= 0
            or retry_max_seconds < retry_base_seconds
            or retry_jitter_seconds < 0
            or transient_max_attempts < 1
            or dependency_failure_threshold < 1
            or dependency_cooldown_seconds <= 0
            or failure_pause_seconds <= 0
        ):
            raise ValueError("WORKER_RETRY_POLICY_INVALID")
        self.queue = queue
        self.repository = repository
        self.storage = storage
        self.parser_router = parser_router
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.chunker = chunker
        self.indexing_sink = indexing_sink
        self.tracer = tracer or InMemoryTracer()
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.retry_jitter_seconds = retry_jitter_seconds
        self.transient_max_attempts = transient_max_attempts
        self.dependency_failure_threshold = dependency_failure_threshold
        self.dependency_cooldown_seconds = dependency_cooldown_seconds
        self.failure_pause_seconds = failure_pause_seconds
        self.clock = clock
        self.jitter = jitter
        self.last_failure: WorkerFailure | None = None
        self.last_idle_delay_seconds = 0.0
        self._consecutive_dependency_failures = 0
        self._dependency_circuit_open_until = 0.0

    @staticmethod
    def _dependency_error(error: BaseException) -> bool:
        module = type(error).__module__.split(".", 1)[0]
        return isinstance(
            error, (TransientProviderError, TimeoutError, ConnectionError, OSError)
        ) or (module in {"httpx", "pymilvus", "pymysql", "redis"})

    def _retry_delay(self, attempt: int) -> float:
        exponential = min(
            self.retry_max_seconds,
            self.retry_base_seconds * (2 ** max(0, attempt - 1)),
        )
        return float(exponential + self.jitter(0.0, self.retry_jitter_seconds))

    def _set_failure(
        self,
        job: QueueJob | None,
        error: BaseException,
        *,
        error_code: str,
        retryable: bool,
        retry_delay_seconds: float,
        dependency_failure: bool,
    ) -> None:
        payload = job.payload if job is not None else {}
        self.last_failure = WorkerFailure(
            job_id=job.id if job is not None else "",
            document_id=str(payload.get("document_id", "")),
            attempt=job.attempt if job is not None else 0,
            error_code=error_code,
            exception_type=type(error).__name__,
            trace_id=str(payload.get("trace_id", f"worker:{self.worker_id}")),
            retryable=retryable,
            retry_delay_seconds=retry_delay_seconds,
            dependency_failure=dependency_failure,
        )

    def _record_dependency_failure(self, job: QueueJob | None, error: BaseException) -> float:
        self._consecutive_dependency_failures += 1
        delay = self._retry_delay(job.attempt if job is not None else 1)
        if self._consecutive_dependency_failures >= self.dependency_failure_threshold:
            self._dependency_circuit_open_until = self.clock() + self.dependency_cooldown_seconds
            delay = max(delay, self.dependency_cooldown_seconds)
        return delay

    def run_once(self) -> bool:
        owner = f"{self.worker_id}:{uuid.uuid4().hex}"
        self.last_failure = None
        self.last_idle_delay_seconds = 0.0
        remaining_cooldown = self._dependency_circuit_open_until - self.clock()
        if remaining_cooldown > 0:
            self.last_idle_delay_seconds = remaining_cooldown
            return False
        try:
            job = self.queue.lease(owner, lease_seconds=self.lease_seconds)
        except Exception as error:
            if not self._dependency_error(error):
                raise
            delay = self._record_dependency_failure(None, error)
            self._set_failure(
                None,
                error,
                error_code="DEPENDENCY_LEASE_UNAVAILABLE",
                retryable=True,
                retry_delay_seconds=delay,
                dependency_failure=True,
            )
            self.last_idle_delay_seconds = delay
            return False
        if job is None:
            return False
        if job.operation != "process_document":
            self.queue.fail(job.id, owner, "JOB_OPERATION_UNSUPPORTED", retryable=False)
            self._set_failure(
                job,
                ValueError("unsupported operation"),
                error_code="JOB_OPERATION_UNSUPPORTED",
                retryable=False,
                retry_delay_seconds=self.failure_pause_seconds,
                dependency_failure=False,
            )
            return True
        version_id = str(job.payload.get("document_version_id", ""))
        artifact_key: str | None = None
        initialized = False
        scope = ExitStack()
        guard = LeaseGuard(self.queue, job.id, owner, self.lease_seconds)
        guard.start()
        try:
            guard.check()
            ingestion_scope = getattr(self.repository, "ingestion_scope", None)
            if callable(ingestion_scope):
                scope.enter_context(ingestion_scope(job))
            guard.check()
            version = self.repository.get_version(version_id)
            initialized = True
            source_format = str(job.payload["source_format"])
            source = self.storage.path_for("original", str(version["original_key"]))
            with self.tracer.span("document.parse", {"source_format": source_format}):
                document = self.parser_router.parse(source_format, source, version_id)
            chunking = (
                self._chunk(document, tenant_id=str(job.payload["tenant_id"]))
                if self.chunker is not None
                else None
            )
            if guard.check():
                self.repository.mark_version_cancelled(version_id)
                self.queue.acknowledge_cancel(job.id, owner)
                return True
            artifact_key = str(version["original_key"]).replace(
                f"original/{source.name}", f"artifacts/canonical-document-f{job.fence_token}.json"
            )
            self.repository.record_local_content(
                str(job.payload["document_id"]),
                version_id,
                "artifacts",
                artifact_key,
                "canonical_document",
            )
            self.storage.write_bytes(
                "artifacts",
                artifact_key,
                (json.dumps(document.to_dict(), ensure_ascii=False, sort_keys=True) + "\n").encode(
                    "utf-8"
                ),
            )
            guard.check()
            self.repository.save_canonical_document(document)
            if chunking is not None:
                save_chunking = getattr(self.repository, "save_chunking_result", None)
                if callable(save_chunking):
                    save_chunking(document, chunking)
                if self.indexing_sink is not None:
                    index_ready = self.indexing_sink.index(
                        chunking,
                        document_id=str(job.payload["document_id"]),
                        tenant_id=str(job.payload["tenant_id"]),
                        space_id=str(job.payload["space_id"]),
                        security_projection=SecurityProjection.unapproved(permission_revision=1),
                        cancel_check=guard.check,
                    )
                    if index_ready is not True:
                        raise RuntimeError("INDEX_SAGA_READY_CONFIRMATION_REQUIRED")
                    mark_index_ready = getattr(self.repository, "mark_index_ready", None)
                    guard.check()
                    if callable(mark_index_ready):
                        mark_index_ready(version_id)
            guard.check()
            self.repository.save_quality_report(DocumentQualityReport.from_document(document))
            completed = self.queue.complete(job.id, owner)
            if completed.state is JobState.CANCELLED:
                self.storage.delete("artifacts", artifact_key)
                self.repository.mark_version_cancelled(version_id)
            self._consecutive_dependency_failures = 0
            self._dependency_circuit_open_until = 0.0
        except QueueLeaseError as error:
            self._set_failure(
                job,
                error,
                error_code="INGEST_LEASE_LOST",
                retryable=False,
                retry_delay_seconds=self.failure_pause_seconds,
                dependency_failure=True,
            )
        except IngestionCancelled:
            if artifact_key is not None:
                self.storage.delete("artifacts", artifact_key)
            self.repository.mark_version_cancelled(version_id)
            self.queue.acknowledge_cancel(job.id, owner)
        except ParsingDeferred as error:
            self.repository.mark_version_quarantined(version_id, self.parser_router.revision)
            self.queue.fail(job.id, owner, error.code, retryable=False)
            self._set_failure(
                job,
                error,
                error_code=error.code,
                retryable=False,
                retry_delay_seconds=self.failure_pause_seconds,
                dependency_failure=False,
            )
        except Exception as error:
            if self._dependency_error(error):
                delay = self._record_dependency_failure(job, error)
                can_retry = job.attempt < min(job.max_attempts, self.transient_max_attempts)
                failed = self.queue.fail(
                    job.id,
                    owner,
                    "INGEST_TRANSIENT",
                    retryable=can_retry,
                    retry_delay=delay,
                )
                retryable = failed.state is JobState.RETRY_WAIT
                if not retryable and initialized:
                    self.repository.mark_version_failed(version_id, self.parser_router.revision)
                self._set_failure(
                    job,
                    error,
                    error_code="INGEST_TRANSIENT",
                    retryable=retryable,
                    retry_delay_seconds=delay if retryable else self.failure_pause_seconds,
                    dependency_failure=True,
                )
            elif isinstance(
                error,
                (InvalidProviderResponse, ConfigurationError, SchemaMismatch, ValueError, KeyError),
            ):
                self.queue.fail(job.id, owner, "INGEST_PERMANENT", retryable=False)
                if initialized:
                    self.repository.mark_version_failed(version_id, self.parser_router.revision)
                self._set_failure(
                    job,
                    error,
                    error_code="INGEST_PERMANENT",
                    retryable=False,
                    retry_delay_seconds=self.failure_pause_seconds,
                    dependency_failure=False,
                )
            else:
                self.queue.fail(job.id, owner, "INGEST_UNEXPECTED", retryable=False)
                if initialized:
                    self.repository.mark_version_failed(version_id, self.parser_router.revision)
                self._set_failure(
                    job,
                    error,
                    error_code="INGEST_UNEXPECTED",
                    retryable=False,
                    retry_delay_seconds=self.failure_pause_seconds,
                    dependency_failure=False,
                )
        finally:
            guard.stop()
            scope.close()
        return True

    def _chunk(self, document: Any, *, tenant_id: str) -> Any:
        if self.chunker is None:
            raise RuntimeError("CHUNKER_UNAVAILABLE")
        with self.tracer.span("document.chunk"):
            return self.chunker.chunk(document, tenant_id=tenant_id)
