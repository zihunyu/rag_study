"""Bounded local query decomposition that keeps original conditions in every facet."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from ragkb.domain.retrieval import IndexCandidate

REVISION = "condition-preserving-facets-v1"


def plan_queries(query: str, maximum: int = 4) -> tuple[str, ...]:
    original = query.strip()
    if maximum <= 1:
        return (original,)
    # Never split quoted identifiers or nested conditions into new assertions.
    pieces: list[str] = []
    current: list[str] = []
    closers: list[str] = []
    pairs = {"(": ")", "（": "）", "[": "]", "【": "】", "“": "”", "「": "」", '"': '"', "`": "`"}
    for char in original:
        if closers and char == closers[-1]:
            closers.pop()
        elif char in pairs:
            closers.append(pairs[char])
        if not closers and char in "；;？?。\n、":
            pieces.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    pieces.append("".join(current).strip())
    if len([p for p in pieces if len(p) >= 2]) < 2 and not any(c in original for c in pairs):
        pieces = re.split(r"以及|同时|另外|并且|，(?=如何|是否|哪些|什么|怎么|能否|多少)", original)
    facets = list(dict.fromkeys(p.strip(" ，,。？?；;") for p in pieces if len(p.strip()) >= 2))
    if len(facets) < 2:
        comparison = re.fullmatch(
            r"(?:请)?(?:比较|对比)(.{1,60}?)(?:和|与)(.{1,60}?)的(.{2,100})[？?。]?", original
        )
        facets = (
            [f"{name}的{comparison[3]}" for name in (comparison[1], comparison[2])]
            if comparison
            else []
        )
    if len(facets) < 2:
        return (original,)
    # The complete question remains in every query: dates, negation, permissions,
    # entities and conditional relationships must not disappear during rewriting.
    return (original, *(f"{original}\n检索重点：{facet}" for facet in facets[: maximum - 1]))


def merge_channel(
    lists: Sequence[Sequence[IndexCandidate]], limit: int
) -> tuple[IndexCandidate, ...]:
    if len(lists) == 1:
        return tuple(lists[0])
    scores: dict[tuple[str, str | None], float] = {}
    representatives: dict[tuple[str, str | None], IndexCandidate] = {}
    for items in lists:
        seen: set[tuple[str, str | None]] = set()
        for item in items[:limit]:
            key = (item.chunk_id, item.vector_pk)
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0) + 1 / (60 + max(1, item.rank))
            representatives.setdefault(key, item)
    # Authorization and retired-attempt filtering happen next. Truncating the
    # merged pool here could let stale/inaccessible hits evict valid facet hits.
    # The pool remains bounded by query_count * per_query_limit.
    ordered = sorted(scores, key=lambda key: (-scores[key], key[0], key[1] or ""))
    return tuple(
        replace(representatives[key], rank=i + 1, score=scores[key])
        for i, key in enumerate(ordered)
    )
