"""G1 local Worker that processes persistent queue jobs."""

from __future__ import annotations

import json

from ragkb.contracts.jobs import PersistentJobQueuePort
from ragkb.contracts.ports import ContentStoragePort, ParserRouterPort, ParsingDeferred
from ragkb.contracts.uploads import UploadRepositoryPort
from ragkb.domain.state_machines import JobState
from ragkb.domain.validation import DocumentQualityReport


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
    ) -> None:
        self.queue = queue
        self.repository = repository
        self.storage = storage
        self.parser_router = parser_router
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

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
        try:
            document = self.parser_router.parse(source_format, source, version_id)
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
            self.repository.save_quality_report(DocumentQualityReport.from_document(document))
            completed = self.queue.complete(job.id, self.worker_id)
            if completed.state is JobState.CANCELLED:
                self.storage.delete("artifacts", artifact_key)
                self.repository.mark_version_cancelled(version_id)
        except ParsingDeferred as error:
            self.repository.mark_version_quarantined(version_id, self.parser_router.revision)
            self.queue.fail(job.id, self.worker_id, error.code, retryable=False)
        except Exception:
            failed = self.queue.fail(
                job.id, self.worker_id, "INGEST_INTERNAL", retryable=True, retry_delay=1
            )
            if failed.state is JobState.FAILED_FINAL:
                self.repository.mark_version_failed(version_id, self.parser_router.revision)
            raise
        return True
