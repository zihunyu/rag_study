"""Opt-in, bounded traces of actual QA input; expectations never enter this context."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_current: ContextVar[dict[str, Any] | None] = ContextVar("acceptance_content_trace", default=None)


@contextmanager
def capture_content() -> Iterator[dict[str, Any]]:
    value: dict[str, Any] = {}
    token = _current.set(value)
    try:
        yield value
    finally:
        _current.reset(token)


def content_stage(stage: str, rows: list[dict[str, Any]], *, mode: str = "model") -> None:
    current = _current.get()
    if current is None:
        return
    # Keep the first generation input paired with the initial draft. Repair
    # calls must not silently replace it with a different evidence package.
    if stage == "model_input" and stage in current:
        current["repair_input_calls"] = current.get("repair_input_calls", 0) + 1
        return
    if sum(len(r.get("text") or "") for r in rows) > 2_000_000:
        current[stage] = {"recorded": False, "reason": "TRACE_CAPACITY_EXCEEDED", "rows": []}
        return
    current[stage] = {"recorded": True, "mode": mode, "rows": rows}


def evidence_rows(evidence: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": e.chunk_id,
            "document_id": e.document_id,
            "version_id": e.document_version_id,
            "text": e.text,
            "locator": e.locator,
        }
        for e in evidence
    ]
