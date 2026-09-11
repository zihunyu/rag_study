"""Whole-source packing with explicit requirements and retrieval-lane reservations."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from ragkb.domain.question_coverage import required_aspects
from ragkb.domain.rag import Evidence


def source_limit(question: str, configured: int) -> int:
    """Wide questions may need one source per requirement; tokens remain the hard bound."""
    return min(64, max(configured, len(required_aspects(question))))


def pack_sources(
    question: str,
    evidence: Sequence[Evidence],
    *,
    token_limit: int,
    cost: Callable[[Evidence], int],
    maximum: int = 10000,
    aspect_sources: Sequence[dict[str, Any]] = (),
) -> tuple[Evidence, ...]:
    """Never cut a sentence/condition or manufacture a new evidence identity.

    Reservations change order only. Callers must reconcile coverage with the
    returned IDs, since some required groups may not fit a bounded request.
    """
    by_id = {e.evidence_id: e for e in evidence if e.authorized and e.current_version}
    sizes = {key: cost(e) for key, e in by_id.items()}
    groups: list[list[str]] = []
    if aspect_sources:
        groups = [list(row.get("evidence_ids", ())) for row in aspect_sources]
        groups.sort(key=lambda group: sum(sizes.get(key, token_limit + 1) for key in group))
    else:
        lanes: dict[str, list[str]] = {}
        for e in by_id.values():
            for query in e.retrieval_queries:
                lanes.setdefault(query, []).append(e.evidence_id)
        frequency = Counter(key for lane in lanes.values() for key in lane)
        for lane in sorted(
            lanes.values(), key=lambda lane: not any(frequency[k] == 1 for k in lane)
        ):
            groups.extend([[k] for k in sorted(lane, key=lambda k: frequency[k])[:1]])
        # Lexical overlap only proposes a reservation; it never establishes support.
        for aspect in required_aspects(question):
            tokens = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]{2,}", aspect["question"].casefold())
            terms = {
                part
                for t in tokens
                for part in ([t] if t.isascii() else [t[i : i + 2] for i in range(len(t) - 1)])
            }
            scores = {key: sum(t in e.text.casefold() for t in terms) for key, e in by_id.items()}
            if scores and max(scores.values()) > 0:
                groups.append([max(scores, key=lambda key: scores[key])])
        # Review-only evidence must have a chance before display hits fill the request.
        review = [e.evidence_id for e in by_id.values() if e.source_role == "conflict_context"]
        hits = [e.evidence_id for e in by_id.values() if e.source_role != "conflict_context"]
        for i in range(max(len(review), len(hits))):
            groups.extend([[lane[i]] for lane in (review, hits) if i < len(lane)])
    groups.extend([[key] for key in by_id])
    selected: list[str] = []
    selected_set: set[str] = set()
    used = 0
    for group in groups:
        additions = list(dict.fromkeys(key for key in group if key not in selected_set))
        if any(key not in by_id for key in additions):
            continue
        size = sum(sizes[key] for key in additions)
        if len(selected) + len(additions) <= maximum and used + size <= token_limit:
            selected.extend(additions)
            selected_set.update(additions)
            used += size
    return tuple(by_id[key] for key in selected)
