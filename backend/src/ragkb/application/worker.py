"""G1 local Worker that processes persistent queue jobs."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from ragkb.application.tracing import InMemoryTracer, TracerPort
from ragkb.contracts.jobs import PersistentJobQueuePort
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
    ) -> None: ...


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
    ) -> None:
        self.queue = queue
        self.repository = repository
        self.storage = storage
        self.parser_router = parser_router
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.chunker = chunker
        self.indexing_sink = indexing_sink
        self.tracer = tracer or InMemoryTracer()

    def run_once(self) -> bool:
        job = self.queue.lease(self.worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            return False
        if job.operation != "process_document":
            self.queue.fail(job.id, self.worker_id, "JOB_OPERATION_UNSUPPORTED", retryable=False)
            return True
        version_id = str(job.payload["document_version_id"])
        version = self.repository.get_version(version_id)
        source_format = str(job.payload["source_format"])
        source = self.storage.path_for("original", str(version["original_key"]))
        artifact_key: str | None = None
        try:
            with self.tracer.span("document.parse", {"source_format": source_format}):
                document = self.parser_router.parse(source_format, source, version_id)
            chunking = (
                self._chunk(document, tenant_id=str(job.payload["tenant_id"]))
                if self.chunker is not None
                else None
            )
            heartbeat = self.queue.heartbeat(
                job.id, self.worker_id, lease_seconds=self.lease_seconds
            )
            if heartbeat.cancel_requested:
                self.repository.mark_version_cancelled(version_id)
                self.queue.acknowledge_cancel(job.id, self.worker_id)
                return True
            artifact_key = str(version["original_key"]).replace(
                f"original/{source.name}", "artifacts/canonical-document-v1.json"
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
            self.repository.save_canonical_document(document)
            if chunking is not None:
                save_chunking = getattr(self.repository, "save_chunking_result", None)
                if callable(save_chunking):
                    save_chunking(document, chunking)
                if self.indexing_sink is not None:
                    self.indexing_sink.index(
                        chunking,
                        document_id=str(job.payload["document_id"]),
                        tenant_id=str(job.payload["tenant_id"]),
                        space_id=str(job.payload["space_id"]),
                        security_projection=SecurityProjection.unapproved(permission_revision=1),
                        cancel_check=lambda: (
                            self.queue.heartbeat(
                                job.id, self.worker_id, lease_seconds=self.lease_seconds
                            ).cancel_requested
                        ),
                    )
                    mark_index_ready = getattr(self.repository, "mark_index_ready", None)
                    if callable(mark_index_ready):
                        mark_index_ready(version_id)
            self.repository.save_quality_report(DocumentQualityReport.from_document(document))
            completed = self.queue.complete(job.id, self.worker_id)
            if completed.state is JobState.CANCELLED:
                self.storage.delete("artifacts", artifact_key)
                self.repository.mark_version_cancelled(version_id)
        except IngestionCancelled:
            if artifact_key is not None:
                self.storage.delete("artifacts", artifact_key)
            self.repository.mark_version_cancelled(version_id)
            self.queue.acknowledge_cancel(job.id, self.worker_id)
        except ParsingDeferred as error:
            self.repository.mark_version_quarantined(version_id, self.parser_router.revision)
            self.queue.fail(job.id, self.worker_id, error.code, retryable=False)
        except TransientProviderError:
            failed = self.queue.fail(
                job.id, self.worker_id, "INGEST_TRANSIENT", retryable=True, retry_delay=1
            )
            if failed.state is JobState.FAILED_FINAL:
                self.repository.mark_version_failed(version_id, self.parser_router.revision)
            raise
        except (InvalidProviderResponse, ConfigurationError, SchemaMismatch, ValueError, KeyError):
            self.queue.fail(job.id, self.worker_id, "INGEST_PERMANENT", retryable=False)
            self.repository.mark_version_failed(version_id, self.parser_router.revision)
            raise
        except Exception:
            self.queue.fail(job.id, self.worker_id, "INGEST_UNEXPECTED", retryable=False)
            self.repository.mark_version_failed(version_id, self.parser_router.revision)
            raise
        return True

    def _chunk(self, document: Any, *, tenant_id: str) -> Any:
        if self.chunker is None:
            raise RuntimeError("CHUNKER_UNAVAILABLE")
        with self.tracer.span("document.chunk"):
            return self.chunker.chunk(document, tenant_id=tenant_id)
