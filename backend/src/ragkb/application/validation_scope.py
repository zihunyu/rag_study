"""Share successful immutable index checks only within one search invocation."""

from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from threading import Lock

_checks: ContextVar[tuple[Lock, set[tuple[object, ...]]] | None] = ContextVar(
    "index_validation_checks", default=None
)


def validation_scope[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        token = _checks.set((Lock(), set()))
        try:
            return function(*args, **kwargs)
        finally:
            _checks.reset(token)

    return wrapped


def validate_once(key: tuple[object, ...], check: Callable[[], object]) -> None:
    scope = _checks.get()
    if scope is None:
        check()
        return
    lock, checked = scope
    with lock:
        if key not in checked:
            check()
            checked.add(key)
