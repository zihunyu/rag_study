"""Native-process G1 backend and Worker entry points."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence

import uvicorn

from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker
from ragkb.runtime_components import build_runtime_components


def prepare_local_runtime() -> dict[str, object]:
    components = build_runtime_components()
    return {
        "runtime": "g1_native_python",
        "storage_root": str(components.storage.root),
        "database_path": str(components.database.path),
        "tenant_id": components.tenant_id,
        "space_id": components.space_id,
        "queue_revision": components.queue.revision,
        "real_service_acceptance": False,
    }


def run_backend(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the G1 native Python FastAPI backend")
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
    parser = argparse.ArgumentParser(description="Run the G1 native Python persistent Worker")
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
                    "runtime": "g1_native_python_worker",
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
    )
    if args.once:
        processed = worker.run_once()
        print(
            json.dumps(
                {
                    "runtime": "g1_native_python_worker",
                    "processed": processed,
                    "recovered_expired": recovered,
                    "real_service_acceptance": False,
                },
                sort_keys=True,
            )
        )
        return 0
    print("G1 native Python Worker started")
    try:
        while True:
            processed = worker.run_once()
            if not processed:
                time.sleep(args.poll_seconds or components.settings.queue_poll_interval_seconds)
    except KeyboardInterrupt:
        return 0
