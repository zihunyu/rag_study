"""A task-local cancellation signal shared by parsers and provider operations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar

from ragkb.domain.errors import IngestionCancelled

_check: ContextVar[Callable[[], bool] | None] = ContextVar("ingestion_cancel_check", default=None)


def cancellation_active() -> bool:
    return _check.get() is not None


def check_cancelled() -> None:
    callback = _check.get()
    if callback is not None and callback():
        raise IngestionCancelled("INGEST_CANCELLED")


@contextmanager
def cancellation_scope(callback: Callable[[], bool] | None) -> Iterator[None]:
    token = _check.set(callback if callback is not None else _check.get())
    try:
        check_cancelled()
        yield
    finally:
        _check.reset(token)


def cancellable_sleep(seconds: float, sleeper: Callable[[float], None]) -> None:
    if not cancellation_active():
        sleeper(seconds)
        return
    remaining = seconds
    while remaining > 0:
        check_cancelled()
        interval = min(0.1, remaining)
        sleeper(interval)
        remaining -= interval
    check_cancelled()


def run_cancellable[T](operation: Callable[[], Awaitable[T]]) -> T:
    """Cancel and await HTTP cleanup; never leave a request running in a detached thread."""

    async def run() -> T:
        check_cancelled()
        task = asyncio.ensure_future(operation())
        try:
            while not task.done():
                check_cancelled()
                await asyncio.wait({task}, timeout=0.1)
            check_cancelled()
            return await task
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task

    return asyncio.run(run())
