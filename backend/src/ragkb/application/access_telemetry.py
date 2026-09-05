"""Bounded, app-owned access metrics; business security audits stay durable."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any


class AccessTelemetry:
    """Never block a response; drain accepted work before closing its database pool."""

    def __init__(self, capacity: int = 1024) -> None:
        self._pending: queue.Queue[tuple[Callable[..., Any], tuple[object, ...]]] = queue.Queue(
            capacity
        )
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._dropped = 0
        self._failed = 0
        self._last_warning = 0.0

    def _consume(self) -> None:
        while True:
            try:
                callback, arguments = self._pending.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if self._pending.empty():
                        self._thread = None
                        return
                continue
            try:
                callback(*arguments)
            except Exception:
                self._failed += 1
                if time.monotonic() - self._last_warning >= 60:
                    logging.getLogger(__name__).warning("ACCESS_TELEMETRY_UNAVAILABLE")
                    self._last_warning = time.monotonic()
            finally:
                self._pending.task_done()

    def submit(self, callback: Callable[..., Any], *arguments: object) -> None:
        with self._lock:
            if self._closed:
                self._dropped += 1
                return
            try:
                self._pending.put_nowait((callback, arguments))
            except queue.Full:
                self._dropped += 1
                return
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._consume, name="rag-access-metrics", daemon=True
                )
                self._thread.start()

    def close(self, timeout: float = 15.0) -> bool:
        with self._lock:
            self._closed = True
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                logging.getLogger(__name__).warning("ACCESS_TELEMETRY_SHUTDOWN_TIMEOUT")
                return False
        return True

    def snapshot(self) -> dict[str, int]:
        return {"pending": self._pending.qsize(), "dropped": self._dropped, "failed": self._failed}
