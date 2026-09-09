"""Request-local hooks for durable per-run HTTP call reservations, including retries."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from ragkb.domain.errors import IngestionCancelled


class AcceptancePaused(IngestionCancelled):
    pass


reserve_call: ContextVar[Callable[[], None] | None] = ContextVar("acceptance_reserve", default=None)
observe_call: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "acceptance_observe", default=None
)


def fresh_answer_required() -> bool:
    return reserve_call.get() is not None


@contextmanager
def acceptance_budget(
    reserve: Callable[[], None], observe: Callable[[dict[str, Any]], None]
) -> Iterator[None]:
    first, second = reserve_call.set(reserve), observe_call.set(observe)
    try:
        yield
    finally:
        reserve_call.reset(first)
        observe_call.reset(second)
