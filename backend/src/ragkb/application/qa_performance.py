"""Content-free per-request timings shared with bounded worker contexts."""

from __future__ import annotations

import hashlib
import json
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
    requests: dict[str, str] = field(default_factory=dict)


_current: ContextVar[Performance | None] = ContextVar("qa_performance", default=None)
_stages: ContextVar[tuple[str, ...]] = ContextVar("qa_performance_stages", default=())


@contextmanager
def performance_scope() -> Iterator[None]:
    token = _current.set(Performance())
    stages = _stages.set(())
    try:
        yield
    finally:
        _current.reset(token)
        _stages.reset(stages)


def request_identity(payload: dict[str, Any]) -> str:
    """Per-request ordinal only; never persist prompts, credentials or their hashes."""
    current = _current.get()
    if current is None:
        return ""
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with current.lock:
        return current.requests.setdefault(digest, f"R{len(current.requests) + 1}")


def record_event(kind: str, **values: Any) -> None:
    current = _current.get()
    if current is not None:
        with current.lock:
            if len(current.events) < 512:
                current.events.append(
                    {
                        "kind": kind,
                        "at_seconds": round(time.perf_counter() - current.started, 4),
                        "stage_path": list(_stages.get()),
                        **values,
                    }
                )


@contextmanager
def performance_stage(name: str) -> Iterator[None]:
    token = _stages.set((*_stages.get(), name))
    try:
        yield
    finally:
        _stages.reset(token)


@contextmanager
def timed_stage(name: str, **values: Any) -> Iterator[None]:
    started = time.perf_counter()
    token = _stages.set((*_stages.get(), name))
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
        _stages.reset(token)


def performance_report() -> dict[str, Any]:
    current = _current.get()
    if current is None:
        return {}
    with current.lock:
        return {
            "revision": "qa-performance-v2-call-stages",
            "elapsed_seconds": round(time.perf_counter() - current.started, 4),
            "events": list(current.events),
        }


def summarize_performance(report: dict[str, Any]) -> dict[str, Any]:
    """Exclusive high-level durations; never sum nested stages or parallel HTTP time."""
    if not report:
        return {}
    events = report.get("events", [])
    names = {
        "evidence": ("资料准备", {"rag.ask.evidence.build"}),
        "generation": ("首次生成", {"rag.ask.llm.generate", "rag.ask.source_list.compose"}),
        "verification": ("首次核验", {"rag.ask.claim.verify"}),
        "repair": (
            "修复回答",
            {
                "rag.ask.conditions.repair",
                "rag.ask.citations.repair",
                "rag.ask.answer_surface.rebuild",
            },
        ),
        "reverification": (
            "修复后再次核验",
            {
                "rag.ask.claim.reverify",
                "rag.ask.citations.reverify",
                "rag.ask.answer_surface.reverify",
            },
        ),
        "release": ("权限与引用发布检查", {"rag.ask.permission.final", "rag.ask.citation.verify"}),
    }
    stages: list[dict[str, Any]] = [
        {
            "key": key,
            "label": label,
            "seconds": round(
                sum(
                    float(e.get("seconds", 0))
                    for e in events
                    if e.get("kind") == "stage" and e.get("name") in members
                ),
                4,
            ),
        }
        for key, (label, members) in names.items()
    ]
    calls: list[dict[str, Any]] = []
    seen: set[str] = set()
    for e in events:
        if e.get("kind") != "model_http":
            continue
        identity = e.get("request_id", "")
        calls.append(
            {
                "number": len(calls) + 1,
                "model": e.get("model", ""),
                "role": e.get("role", ""),
                "outcome": e.get("outcome", ""),
                "sent": e.get("sent", False),
                "stage": " / ".join(e.get("stage_path", [])),
                "queue_seconds": e.get("queue_seconds", 0),
                "network_seconds": e.get("network_seconds", 0),
                "same_request_again": bool(identity and identity in seen),
                "identity_recorded": bool(identity),
            }
        )
        if identity:
            seen.add(identity)
    elapsed = report.get("elapsed_seconds")
    return {
        "elapsed_seconds": elapsed,
        "stages": stages,
        "calls": calls,
        "other_seconds": round(max(0, float(elapsed) - sum(r["seconds"] for r in stages)), 4)
        if elapsed is not None
        else None,
        "reverification_count": sum(
            e.get("kind") == "stage" and e.get("name") in names["reverification"][1] for e in events
        ),
        "failed_calls": sum(c["outcome"] != "200" for c in calls),
        "same_request_again_count": sum(c["same_request_again"] for c in calls),
        "cache": [e for e in events if e.get("kind") == "cache"],
        "verification_work": {
            "initial_calls": sum(
                "rag.ask.claim.verify" in c["stage"]
                and "protocol_repair" not in c["stage"]
                and "conditions.retry" not in c["stage"]
                for c in calls
            ),
            "protocol_retry_calls": sum(
                "protocol_repair" in c["stage"] or "conditions.retry" in c["stage"] for c in calls
            ),
            "answer_repair_calls": sum(
                any(name in c["stage"] for name in names["repair"][1]) for c in calls
            ),
            "verified_duplicate_removals": sum(
                e.get("kind") == "answer_projection"
                and e.get("outcome") == "removed_verified_duplicate_table"
                for e in events
            ),
            "citation_assemblies": sum(e.get("kind") == "citation_assembly" for e in events),
        },
        "retrieval_rounds": [e for e in events if e.get("kind") == "retrieval_round"],
        "reading_scope": [e for e in events if e.get("kind") == "reading_scope"],
    }
