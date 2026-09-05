"""A single monotonic request budget shared by all synchronous provider stages."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Semaphore

from ragkb.domain.errors import ProviderTimeout

_deadline: ContextVar[float | None] = ContextVar("rag_request_deadline", default=None)


def remaining_timeout(timeout: float) -> float:
    deadline = _deadline.get()
    remaining = min(timeout, deadline - time.monotonic()) if deadline is not None else timeout
    if remaining <= 0:
        raise ProviderTimeout("REQUEST_DEADLINE_EXCEEDED")
    return remaining


@contextmanager
def request_deadline(seconds: float = 120) -> Iterator[None]:
    token = _deadline.set(time.monotonic() + remaining_timeout(seconds))
    try:
        yield
        remaining_timeout(seconds)
    finally:
        _deadline.reset(token)


@contextmanager
def bounded_slot(semaphore: Semaphore, timeout: float) -> Iterator[None]:
    if not semaphore.acquire(timeout=remaining_timeout(timeout)):
        raise ProviderTimeout("MODEL_CONCURRENCY_WAIT_TIMEOUT")
    try:
        yield
    finally:
        semaphore.release()
