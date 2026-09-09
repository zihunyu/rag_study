"""Content-free per-request timings shared with bounded worker contexts."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class Performance:
    started: float = field(default_factory=time.perf_counter)
    events: list[dict[str, Any]] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)


_current: ContextVar[Performance | None] = ContextVar("qa_performance", default=None)


@contextmanager
def performance_scope() -> Iterator[None]:
    token = _current.set(Performance())
    try:
        yield
    finally:
        _current.reset(token)


def record_event(kind: str, **values: Any) -> None:
    current = _current.get()
    if current is not None:
        with current.lock:
            if len(current.events) < 512:
                current.events.append({"kind": kind, **values})


@contextmanager
def timed_stage(name: str, **values: Any) -> Iterator[None]:
    started = time.perf_counter()
    status = "ok"
    try:
        yield
    except BaseException:
        status = "failed"
        raise
    finally:
        record_event(
            "stage",
            name=name,
            seconds=round(time.perf_counter() - started, 4),
            status=status,
            **values,
        )


def performance_report() -> dict[str, Any]:
    current = _current.get()
    if current is None:
        return {}
    with current.lock:
        return {
            "revision": "qa-performance-v1",
            "elapsed_seconds": round(time.perf_counter() - current.started, 4),
            "events": list(current.events),
        }
