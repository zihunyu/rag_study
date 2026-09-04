"""Native-process G1-G4-local backend and Worker entry points."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TextIO

import uvicorn

from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker, WorkerFailure
from ragkb.runtime_components import build_runtime_components


@dataclass(frozen=True)
class WorkerIteration:
    processed: bool
    failed: bool
    sleep_seconds: float = 0.0


def _write_worker_failure(failure: object, stream: TextIO) -> None:
    print(
        json.dumps(
            {
                "runtime": "g3_native_python_worker",
                "event": "task_failed",
                "error_code": str(getattr(failure, "error_code", "WORKER_TASK_FAILED")),
                "job_id": str(getattr(failure, "job_id", "")),
                "document_id": str(getattr(failure, "document_id", "")),
                "attempt": int(getattr(failure, "attempt", 0)),
                "exception_type": str(getattr(failure, "exception_type", "UnknownError")),
                "trace_id": str(getattr(failure, "trace_id", "")),
                "retryable": bool(getattr(failure, "retryable", False)),
                "retry_delay_seconds": float(getattr(failure, "retry_delay_seconds", 0.0)),
                "dependency_failure": bool(getattr(failure, "dependency_failure", False)),
                "secret_values_in_output": False,
            },
            sort_keys=True,
        ),
        file=stream,
    )


def run_worker_iteration(
    worker: LocalIngestionWorker, *, error_stream: TextIO | None = None
) -> WorkerIteration:
    try:
        processed = worker.run_once()
        failure = getattr(worker, "last_failure", None)
        if failure is not None:
            _write_worker_failure(failure, error_stream or sys.stderr)
            return WorkerIteration(
                processed=processed,
                failed=True,
                sleep_seconds=float(getattr(failure, "retry_delay_seconds", 1.0)),
            )
        return WorkerIteration(
            processed=processed,
            failed=False,
            sleep_seconds=float(getattr(worker, "last_idle_delay_seconds", 0.0)),
        )
    except Exception as error:
        fallback = WorkerFailure(
            job_id="",
            document_id="",
            attempt=0,
            error_code="WORKER_CONTROL_PATH_FAILED",
            exception_type=type(error).__name__,
            trace_id="",
            retryable=True,
            retry_delay_seconds=float(getattr(worker, "failure_pause_seconds", 1.0)),
            dependency_failure=True,
        )
        _write_worker_failure(fallback, error_stream or sys.stderr)
        return WorkerIteration(
            processed=False,
            failed=True,
            sleep_seconds=fallback.retry_delay_seconds,
        )


def prepare_local_runtime() -> dict[str, object]:
    components = build_runtime_components()
    return {
        "runtime": "g3_native_python",
        "storage_root": str(components.storage.root),
        "database_path": str(components.database.path),
        "tenant_id": components.tenant_id,
        "space_id": components.space_id,
        "queue_revision": components.queue.revision,
        "real_service_acceptance": False,
    }


def run_backend(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the G3 native Python FastAPI backend")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    components = build_runtime_components()
    app = create_app(components)
    if args.check:
        status = prepare_local_runtime()
        status["openapi_version"] = app.version
        status["openapi_path_count"] = len(app.openapi()["paths"])
        print(json.dumps(status, ensure_ascii=False, sort_keys=True))
        return 0
    uvicorn.run(
        app,
        host=args.host or components.settings.app_host,
        port=args.port or components.settings.app_port,
        log_level=components.settings.log_level.casefold(),
    )
    return 0


def run_worker(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the G3 native Python persistent Worker")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float)
    parser.add_argument("--worker-id", default="local-worker-1")
    args = parser.parse_args(argv)
    components = build_runtime_components()
    if args.check:
        print(
            json.dumps(
                {
                    "runtime": "g3_native_python_worker",
                    "queue_revision": components.queue.revision,
                    "database_path": str(components.database.path),
                    "real_service_acceptance": False,
                },
                sort_keys=True,
            )
        )
        return 0
    recovered = components.queue.recover_expired()
    worker = LocalIngestionWorker(
        components.queue,
        components.repository,
        components.storage,
        components.parser_router,
        args.worker_id,
        lease_seconds=components.settings.queue_lease_seconds,
        chunker=components.chunker,
        indexing_sink=components.indexing_sink,
        tracer=components.tracer,
        retry_base_seconds=components.settings.queue_retry_delay_seconds,
        retry_max_seconds=components.settings.worker_retry_max_delay_seconds,
        retry_jitter_seconds=components.settings.worker_retry_jitter_seconds,
        transient_max_attempts=components.settings.worker_transient_max_attempts,
        dependency_failure_threshold=components.settings.worker_dependency_failure_threshold,
        dependency_cooldown_seconds=components.settings.worker_dependency_cooldown_seconds,
        failure_pause_seconds=components.settings.worker_failure_pause_seconds,
    )
    if args.once:
        iteration = run_worker_iteration(worker)
        print(
            json.dumps(
                {
                    "runtime": "g3_native_python_worker",
                    "processed": iteration.processed,
                    "failed": iteration.failed,
                    "retry_delay_seconds": iteration.sleep_seconds,
                    "recovered_expired": recovered,
                    "real_service_acceptance": False,
                },
                sort_keys=True,
            )
        )
        return 1 if iteration.failed else 0
    print("G3 native Python Worker started")
    try:
        while True:
            iteration = run_worker_iteration(worker)
            if iteration.sleep_seconds > 0:
                time.sleep(iteration.sleep_seconds)
            elif not iteration.processed:
                time.sleep(args.poll_seconds or components.settings.queue_poll_interval_seconds)
    except KeyboardInterrupt:
        return 0
