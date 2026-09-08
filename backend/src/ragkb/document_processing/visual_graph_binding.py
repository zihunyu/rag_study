"""Bind graph numerals to their own measured text, never the whole image's number set."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ragkb.domain.graph_facts import accepted_graph_projection
from ragkb.domain.visual_graph import Graph
from ragkb.domain.visual_numbers import compact, critical_tokens


def _inside(region: dict[str, Any], box: list[float]) -> bool:
    measured = region.get("bbox")
    if not isinstance(measured, list) or len(measured) != 4:
        return False
    x, y = (measured[0] + measured[2]) / 2, (measured[1] + measured[3]) / 2
    return bool(box[0] <= x <= box[2] and box[1] <= y <= box[3])


def _tokens(text: str) -> Counter[str]:
    return Counter(compact(token) for token in critical_tokens(text))


def _owner(text: str) -> str:
    for token in critical_tokens(text):
        text = text.replace(token, " ")
    return compact(re.sub(r"[^\w\u3400-\u9fff]+", "", text))


def _bbox(regions: list[dict[str, Any]]) -> list[float] | None:
    boxes = [r["bbox"] for r in regions if isinstance(r.get("bbox"), list) and len(r["bbox"]) == 4]
    if len(boxes) != len(regions) or not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def bind_graph(graph: Graph, regions: list[dict[str, Any]], index: int) -> dict[str, Any]:
    projected = accepted_graph_projection(graph)
    accepted = {id(obj) for obj in [*projected.nodes, *projected.groups, *projected.edges]}
    measured = [r for r in regions if r.get("score", 0) >= 0.9]
    issues: list[str] = []
    targets: list[dict[str, Any]] = []
    disputed = False
    objects: list[tuple[str, Any, dict[str, Any], str]] = (
        [("节点", n, {"kind": "node", "node_id": n.id}, n.label) for n in graph.nodes]
        + [("分组", g, {"kind": "group", "group_id": g.id}, g.label) for g in graph.groups]
        + [
            (
                "连线",
                e,
                {"kind": "edge", "edge_index": i},
                e.label
                + (
                    "\n" + getattr(e, "condition", "")
                    if getattr(e, "condition", "") != e.label
                    else ""
                ),
            )
            for i, e in enumerate(graph.edges)
        ]
    )
    for position, (kind, obj, identity, text) in enumerate(objects, 1):
        if id(obj) not in accepted:
            continue
        trusted_box = getattr(obj, "bbox", None)
        if getattr(obj, "bbox_basis", "unverified") not in {"ocr", "human"}:
            trusted_box = None
        candidates = [r for r in measured if trusted_box is None or _inside(r, trusted_box)]
        exact = [r for r in candidates if compact(r["text"]) == compact(text) and text.strip()]
        # A full label with a nonnumeric identity can bind itself uniquely. A bare numeral
        # on a node/edge needs its own confirmed original-image region to prove ownership.
        bound = (
            exact if len(exact) == 1 and (not _tokens(text) or _owner(text) or trusted_box) else []
        )
        expected = _tokens(text)
        if not bound and expected and trusted_box and candidates:
            ordered = sorted(candidates, key=lambda r: (round(r["bbox"][1], 2), r["bbox"][0]))
            joined = "\n".join(r["text"] for r in ordered)
            if compact(joined) == compact(text):
                bound = ordered
        state = "consistent" if bound else "not_required"
        if expected and not bound:
            owner = _owner(text)
            related = (
                candidates
                if trusted_box
                else [r for r in candidates if owner and _owner(r["text"]) == owner]
            )
            disagreement = (
                _tokens("\n".join(r["text"] for r in related)) != expected
                if trusted_box
                else all(_tokens(r["text"]) != expected for r in related)
            )
            if related and disagreement:
                state, disputed = "disagreement", True
                issues.append(
                    f"关系图 {index + 1} {kind} {position} 的数字或单位与其原图区域不一致"
                )
            else:
                state = "inconclusive"
                issues.append(f"关系图 {index + 1} {kind} {position} 的数字尚未绑定到唯一原图区域")
        targets.append(
            {
                **identity,
                "graph": index,
                "region_ids": [r["id"] for r in bound],
                "bbox": _bbox(bound),
                "bbox_basis": "ocr" if _bbox(bound) else "unverified",
                "status": state,
            }
        )
    return {
        "status": "disagreement" if disputed else "inconclusive" if issues else "consistent",
        "issues": issues,
        "targets": targets,
    }
