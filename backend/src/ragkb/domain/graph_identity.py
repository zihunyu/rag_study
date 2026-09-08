"""Bounded identity matching without treating visible labels as component identifiers."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from ragkb.domain.visual_graph import Graph, LocatedElement


def compact(text: str) -> str:
    # A decision's full/half-width question mark does not change its premise. Preserve
    # case, digits and operators: MW/mW and < or > must never be conflated.
    return re.sub(r"\s+", "", text).replace("?", "？")


@dataclass(frozen=True)
class GraphMatch:
    mapping: dict[str, str] | None
    exhausted: bool = False


def _relations(graph: Graph) -> dict[tuple[str, str], Counter[str]]:
    result: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for item in graph.groups:
        if item.parent:
            result[item.parent, item.id]["contains-group"] += 1
    for node in graph.nodes:
        if node.group:
            result[node.group, node.id]["contains-node"] += 1
    for edge in graph.edges:
        signature = repr((edge.direction, edge.style, compact(edge.label), compact(edge.condition)))
        result[edge.source, edge.target][signature] += 1
        if edge.direction in {"both", "none"} and edge.source != edge.target:
            result[edge.target, edge.source][signature] += 1
    return result


def _seeds(graph: Graph) -> dict[str, str]:
    return {
        **{g.id: repr(("group", compact(g.label), g.parent is None)) for g in graph.groups},
        **{n.id: repr(("node", compact(n.label), n.shape, n.group is None)) for n in graph.nodes},
    }


def _near(first: LocatedElement, second: LocatedElement) -> bool:
    # Model-estimated coordinates cannot prove identity. Independent OCR/human anchors can.
    if (
        first.bbox is None
        or second.bbox is None
        or first.bbox_basis == "unverified"
        or second.bbox_basis == "unverified"
    ):
        return True
    ax0, ay0, ax1, ay1 = first.bbox
    bx0, by0, bx1, by1 = second.bbox
    overlap = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))
    return overlap / min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0)) >= 0.5


def match_graphs(first: Graph, second: Graph, *, max_steps: int = 10000) -> GraphMatch:
    """Find a full typed multigraph isomorphism, including containment and edge multiplicity.

    Symmetric repeated instances may have several equivalent correspondences: any complete
    correspondence is sufficient because it preserves *all* relationships. Hitting a bound
    is inconclusive, never an approval. IDs and list order are deliberately not compared.
    """
    if (len(first.groups), len(first.nodes), len(first.edges)) != (
        len(second.groups),
        len(second.nodes),
        len(second.edges),
    ):
        return GraphMatch(None)
    ra, rb = _relations(first), _relations(second)
    ca, cb = _seeds(first), _seeds(second)
    for _ in range(len(ca) + 1):
        signatures: list[dict[str, str]] = []
        for colors, relations in ((ca, ra), (cb, rb)):
            incoming: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
            outgoing: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
            for (left, right), attributes in relations.items():
                for attr, count in attributes.items():
                    outgoing[left].append((attr, colors[right], count))
                    incoming[right].append((attr, colors[left], count))
            signatures.append(
                {
                    key: repr((color, sorted(incoming[key]), sorted(outgoing[key])))
                    for key, color in colors.items()
                }
            )
        vocabulary = {
            value: str(i)
            for i, value in enumerate(
                sorted(set().union(signatures[0].values(), signatures[1].values()))
            )
        }
        na, nb = ({key: vocabulary[value] for key, value in item.items()} for item in signatures)
        if Counter(na.values()) != Counter(nb.values()):
            return GraphMatch(None)
        stable = len(set(na.values())) == len(set(ca.values()))
        ca, cb = na, nb
        if stable:
            break
    elements_a: dict[str, LocatedElement] = {
        **{g.id: g for g in first.groups},
        **{n.id: n for n in first.nodes},
    }
    elements_b: dict[str, LocatedElement] = {
        **{g.id: g for g in second.groups},
        **{n.id: n for n in second.nodes},
    }
    candidates = {
        key: [
            other
            for other in cb
            if ca[key] == cb[other] and _near(elements_a[key], elements_b[other])
        ]
        for key in ca
    }
    if any(not values for values in candidates.values()):
        return GraphMatch(None)
    assignment: dict[str, str] = {}
    used: set[str] = set()
    steps = 0
    exhausted = False
    empty: Counter[str] = Counter()

    def search() -> bool:
        nonlocal steps, exhausted
        if len(assignment) == len(ca):
            return True
        key = min(
            (key for key in ca if key not in assignment),
            key=lambda k: sum(other not in used for other in candidates[k]),
        )
        for other in candidates[key]:
            if other in used:
                continue
            steps += 1
            if steps > max_steps:
                exhausted = True
                return False
            if ra.get((key, key), empty) != rb.get((other, other), empty):
                continue
            if any(
                ra.get((key, left), empty) != rb.get((other, right), empty)
                or ra.get((left, key), empty) != rb.get((right, other), empty)
                for left, right in assignment.items()
            ):
                continue
            assignment[key] = other
            used.add(other)
            if search():
                return True
            used.remove(other)
            del assignment[key]
            if exhausted:
                return False
        return False

    return GraphMatch(dict(assignment) if search() else None, exhausted)


def endpoint_labels(graph: Graph, *, reference: Graph | None = None) -> dict[str, str]:
    """Readable, complete scope paths; unnamed containers retain their actual membership."""
    groups = {g.id: g for g in graph.groups}
    labels: dict[str, str] = {}
    identity_graph = reference or graph
    duplicates = Counter((n.group, compact(n.label)) for n in identity_graph.nodes)
    group_duplicates = Counter((g.parent, compact(g.label)) for g in identity_graph.groups)

    def group_label(key: str) -> str:
        if key in labels:
            return labels[key]
        group = groups[key]
        name = group.label.strip()
        if not name:
            members = [n.label.strip() or "未命名组件" for n in graph.nodes if n.group == key]
            members.extend(
                g.label.strip() or "未命名子区域" for g in graph.groups if g.parent == key
            )
            name = "未命名区域" + (f"（包含：{'、'.join(members)}）" if members else "")
        if group_duplicates[group.parent, compact(group.label)] > 1:
            peers = [
                g.id
                for g in identity_graph.groups
                if g.parent == group.parent and compact(g.label) == compact(group.label)
            ]
            name += f"〔区域 {peers.index(key) + 1}〕"
        labels[key] = (group_label(group.parent) + " / " if group.parent else "") + name
        return labels[key]

    for group in graph.groups:
        group_label(group.id)
    for node in graph.nodes:
        name = node.label.strip() or "未命名组件"
        if duplicates[node.group, compact(node.label)] > 1:
            peers = [
                n.id
                for n in identity_graph.nodes
                if n.group == node.group and compact(n.label) == compact(node.label)
            ]
            name += f"〔实例 {peers.index(node.id) + 1}〕"
        labels[node.id] = (group_label(node.group) + " / " if node.group else "") + name
    return labels
