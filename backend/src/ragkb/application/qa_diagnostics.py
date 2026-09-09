"""Bounded request-local diagnostics, persisted privately with failed RAG packages.

No HTTP headers, URLs or credentials are recorded. Content stays out of the public
answer, tracing and conversation APIs. A local operator can inspect the run store.
"""

from __future__ import annotations

import contextvars
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

MAX_CHARACTERS = 512_000
MAX_CALLS = 8


@dataclass
class QADiagnostics:
    calls: list[dict[str, Any]] = field(default_factory=list)
    failure: dict[str, Any] = field(default_factory=dict)
    characters: int = 0
    truncated: bool = False
    lock: RLock = field(default_factory=RLock)

    def record(self, value: dict[str, Any]) -> None:
        with self.lock:
            self._record(value)

    def _record(self, value: dict[str, Any]) -> None:
        rendered = json.dumps(value, ensure_ascii=False, default=str)
        if len(rendered) > MAX_CHARACTERS:
            self.truncated = True
            value = {
                "adapter": value["adapter"],
                "model": value["model"],
                "truncated": True,
                "response": str(value.get("response", ""))[:64_000],
            }
            rendered = json.dumps(value, ensure_ascii=False)
        while self.calls and (
            len(self.calls) >= MAX_CALLS or self.characters + len(rendered) > MAX_CHARACTERS
        ):
            self.characters -= len(json.dumps(self.calls.pop(0), ensure_ascii=False, default=str))
            self.truncated = True
        self.calls.append(value)
        self.characters += len(rendered)


_current: contextvars.ContextVar[QADiagnostics | None] = contextvars.ContextVar(
    "qa_diagnostics", default=None
)


@contextmanager
def diagnostic_scope() -> Iterator[None]:
    token = _current.set(QADiagnostics())
    try:
        yield
    finally:
        _current.reset(token)


def record_model_call(
    adapter: str,
    payload: Mapping[str, Any],
    response: Mapping[str, Any] | None,
    elapsed_seconds: float,
    error_type: str = "",
) -> None:
    current = _current.get()
    # Embedding vectors and reranker requests do not help diagnose answer checks.
    if current is None or "messages" not in payload:
        return
    current.record(
        {
            "adapter": adapter,
            "model": payload.get("model"),
            "messages": payload.get("messages"),
            "max_tokens": payload.get("max_tokens"),
            "response": {
                key: response[key]
                for key in ("choices", "usage")
                if response is not None and key in response
            },
            "elapsed_seconds": round(elapsed_seconds, 3),
            "error_type": error_type,
        }
    )


def record_failure(stage: str, code: str, details: Mapping[str, Any] | None = None) -> None:
    current = _current.get()
    if current is not None:
        current.failure = {"stage": stage, "code": code, **(details or {})}


def failure_diagnostics() -> dict[str, Any]:
    current = _current.get()
    if current is None or not current.failure:
        return {}
    return {
        "revision": "qa-failure-diagnostics-v1",
        "failure": current.failure,
        "calls": list(current.calls),
        "truncated": current.truncated,
    }
