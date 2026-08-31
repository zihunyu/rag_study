"""Native-process G0 runtime adapters used by direct Python entry points."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.stubs import InMemoryJobQueue
from ragkb.config.loader import find_repository_root, load_configuration


def _path_value(data: Mapping[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        value = value[part]
    return value


def prepare_local_runtime() -> dict[str, object]:
    root = find_repository_root()
    loaded = load_configuration(root)
    if loaded.schema_errors:
        raise RuntimeError(
            f"runtime configuration has {len(loaded.schema_errors)} schema or path errors"
        )
    configured = Path(str(_path_value(loaded.effective, "infrastructure.local_storage.root_path")))
    storage_root = configured if configured.is_absolute() else root / configured
    storage = LocalFileStorage(storage_root)
    storage.ensure_layout()
    return {
        "runtime": "g0_native_python_stub",
        "storage_root": str(storage.root),
        "stubbed_input_count": len(loaded.stubbed_paths),
        "real_service_acceptance": False,
    }


class _HealthHandler(BaseHTTPRequestHandler):
    runtime_status: dict[str, object] = {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/health/live", "/health/ready"}:
            self.send_error(404)
            return
        payload = json.dumps(self.runtime_status, sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def run_backend(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the G0 native Python backend stub")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    status = prepare_local_runtime()
    if args.check:
        print(json.dumps(status, ensure_ascii=False, sort_keys=True))
        return 0
    _HealthHandler.runtime_status = status
    server = ThreadingHTTPServer((args.host, args.port), _HealthHandler)
    print(f"G0 native Python backend stub listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def run_worker(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the G0 native Python worker stub")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    status = prepare_local_runtime()
    queue = InMemoryJobQueue()
    if args.once:
        print(json.dumps({**status, "worker_poll": "completed", "jobs": 0}, sort_keys=True))
        return 0
    print("G0 native Python worker stub started; no real queue acceptance is implied")
    try:
        while True:
            queue.dequeue()
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0
