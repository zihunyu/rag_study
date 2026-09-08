"""Version-scoped graph evidence and bounded relationship queries over verified JSON."""

from __future__ import annotations

import hashlib
import re
from collections import deque
from dataclasses import dataclass
from typing import Literal

from ragkb.domain.graph_identity import endpoint_labels
from ragkb.domain.visual_graph import Edge, Graph, LocatedElement


@dataclass(frozen=True)
class GraphFact:
    fact_id: str
    kind: Literal["node", "group", "edge"]
    element_id: str
    subject: str
    text: str
    source_id: str = ""
    target_id: str = ""
    source: str = ""
    target: str = ""
    condition: str = ""
    direction: str = ""
    style: str = ""
    bbox: tuple[float, ...] | None = None
    bbox_basis: str = "unverified"
    review_status: str = "inherited"


@dataclass(frozen=True)
class GraphQueryResult:
    facts: tuple[GraphFact, ...]
    paths: tuple[tuple[str, ...], ...] = ()
    truncated: bool = False
    gaps: tuple[str, ...] = ()


def accepted_graph_projection(graph: Graph, *, verified: bool = True) -> Graph:
    """Exclude entire unconfirmed containment scopes and all incident relationships.

    The original remains intact for review/audit. Unscoped uncertainties cannot be silently
    converted to partial approval: they must first be resolved or assigned to elements.
    """

    def accepted(element: LocatedElement) -> bool:
        return element.review_status == "confirmed" or (
            verified and element.review_status == "inherited"
        )

    if graph.uncertainties:
        return graph.model_copy(update={"groups": [], "nodes": [], "edges": []})
    group_ids = {g.id for g in graph.groups if accepted(g)}
    changed = True
    while changed:
        invalid = {
            g.id
            for g in graph.groups
            if g.id in group_ids and g.parent and g.parent not in group_ids
        }
        changed = bool(invalid)
        group_ids -= invalid
    nodes = [n for n in graph.nodes if accepted(n) and (not n.group or n.group in group_ids)]
    endpoints = group_ids | {n.id for n in nodes}
    return graph.model_copy(
        update={
            "groups": [g for g in graph.groups if g.id in group_ids],
            "nodes": nodes,
            "edges": [
                e
                for e in graph.edges
                if accepted(e) and e.source in endpoints and e.target in endpoints
            ],
        }
    )


def edge_condition(graph: Graph, edge: Edge, labels: dict[str, str]) -> str:
    node = next((node for node in graph.nodes if node.id == edge.source), None)
    parts = [edge.condition.strip()] if edge.condition.strip() else []
    if node and edge.direction != "none":
        # Geometry does not establish business meaning: architecture icons and pyramid
        # diagrams can use diamonds too. Require printed decision wording or a visible
        # conditional branch; do not manufacture a missing branch for an ordinary icon.
        question = re.search(
            r"[?？]\s*$|是否|能否|有无|^(?:判断|判定)[：:\s]|\b(?:whether|if)\b",
            node.label,
            re.IGNORECASE,
        )
        branch = edge.label.strip() or edge.condition.strip()
        explicit_branch = re.fullmatch(
            r"是|否|成功|失败|正常|异常|通过|未通过|不通过|满足|不满足|"
            r"yes|no|true|false|success|failure|passed|failed",
            branch,
            re.IGNORECASE,
        )
        if question:
            parts.append(f"判断：{labels[node.id]}；分支：{branch or '原图未标明条件'}")
        elif explicit_branch:
            # An action can also have success/failure outputs. Retain the condition
            # without declaring that the component itself is a decision operation.
            parts.append(f"条件分支起点：{labels[node.id]}；分支：{branch}")
    return "；".join(dict.fromkeys(parts))


def graph_facts(graph: Graph, *, scope: str, verified: bool) -> list[GraphFact]:
    if not scope.strip():
        raise ValueError("GRAPH_EVIDENCE_SCOPE_REQUIRED")
    projection = accepted_graph_projection(graph, verified=verified)
    labels = endpoint_labels(projection, reference=graph)
    result: list[GraphFact] = []

    def fact_id(kind: str, key: str) -> str:
        return "GF-" + hashlib.sha256(f"{scope}\0{kind}\0{key}".encode()).hexdigest()[:24]

    for kind, elements in (("group", projection.groups), ("node", projection.nodes)):
        for element in elements:
            result.append(
                GraphFact(
                    fact_id=fact_id(kind, element.id),
                    kind="group" if kind == "group" else "node",
                    element_id=element.id,
                    subject=labels[element.id],
                    text=f"{'分组' if kind == 'group' else '组件'}：{labels[element.id]}",
                    bbox=tuple(element.bbox) if element.bbox else None,
                    bbox_basis=element.bbox_basis,
                    review_status=element.review_status,
                )
            )
    relation = {"forward": "指向", "both": "双向连接", "none": "相连（无箭头）"}
    for index, edge in enumerate(graph.edges):
        if edge not in projection.edges:
            continue
        condition = edge_condition(projection, edge, labels)
        text = f"{labels[edge.source]} {relation[edge.direction]} {labels[edge.target]}"
        if edge.label:
            text += f"；连线标注：{edge.label}"
        if condition:
            text += f"；{condition}"
        if edge.style == "dashed":
            text += "；虚线（线型含义以原图说明为准）"
        result.append(
            GraphFact(
                fact_id=fact_id("edge", str(index)),
                kind="edge",
                element_id=f"edge:{index}",
                subject=labels[edge.source],
                text=text,
                source_id=edge.source,
                target_id=edge.target,
                source=labels[edge.source],
                target=labels[edge.target],
                condition=condition,
                direction=edge.direction,
                style=edge.style,
                bbox=tuple(edge.bbox) if edge.bbox else None,
                bbox_basis=edge.bbox_basis,
                review_status=edge.review_status,
            )
        )
    return result


def query_graph(
    graph: Graph,
    *,
    scope: str,
    verified: bool,
    source_ids: tuple[str, ...] | list[str],
    target_ids: tuple[str, ...] | list[str] = (),
    mode: Literal["direct", "paths", "branches"] = "direct",
    max_hops: int = 6,
    max_results: int = 30,
) -> GraphQueryResult:
    """Query explicit endpoints only; names must be resolved without guessing upstream.

    Directed paths never traverse arrowless lines, containment, or an excluded edge.
    Paths retain every branch condition. They describe connectivity, not an assertion of
    execution order, concurrency, reachability under all inputs, or successful completion.
    """
    max_hops = min(12, max(1, max_hops))
    max_results = min(100, max(1, max_results))
    facts = graph_facts(graph, scope=scope, verified=verified)
    edges = [f for f in facts if f.kind == "edge"]
    all_ids = {f.element_id for f in facts if f.kind != "edge"}
    gaps: list[str] = []
    sources = list(dict.fromkeys(source_ids))
    targets = set(target_ids)
    if any(key not in all_ids for key in [*sources, *targets]):
        gaps.append("查询端点不存在或尚未确认，不能推断其关系")
    if (
        graph.uncertainties
        or len(edges) != len(graph.edges)
        or len(all_ids) != len(graph.groups) + len(graph.nodes)
    ):
        gaps.append("图中仍有未确认或排除的关系，路径覆盖不完整")

    def coverage_gaps(selected: list[GraphFact]) -> tuple[str, ...]:
        if any("原图未标明条件" in fact.condition for fact in selected):
            return (*gaps, "原图判断分支未标明适用条件，不能推断其前提")
        return tuple(gaps)

    if mode == "direct":
        selected = [
            e
            for e in edges
            if (e.source_id in sources or e.target_id in sources)
            and (not targets or e.target_id in targets or e.source_id in targets)
        ]
        return GraphQueryResult(
            tuple(selected[:max_results]),
            truncated=len(selected) > max_results,
            gaps=coverage_gaps(selected[:max_results]),
        )
    adjacency: dict[str, list[tuple[str, GraphFact]]] = {}
    for edge in edges:
        if edge.direction == "none":
            continue
        adjacency.setdefault(edge.source_id, []).append((edge.target_id, edge))
        if edge.direction == "both":
            adjacency.setdefault(edge.target_id, []).append((edge.source_id, edge))
    if mode == "branches":
        # Starting from one entry means following its reachable decisions, not merely
        # returning the first outgoing step. Keep real loop edges but expand a node once.
        frontier = deque((source, 0) for source in sources if source in all_ids)
        expanded: set[str] = set()
        selected_branches: dict[str, GraphFact] = {}
        truncated = False
        while frontier:
            current, depth = frontier.popleft()
            if current in expanded:
                continue
            expanded.add(current)
            if current in targets:
                continue
            outgoing = adjacency.get(current, [])
            if depth >= max_hops:
                truncated |= any(edge.fact_id not in selected_branches for _, edge in outgoing)
                continue
            for next_id, edge in outgoing:
                if edge.fact_id not in selected_branches:
                    if len(selected_branches) >= max_results:
                        truncated = True
                        continue
                    selected_branches[edge.fact_id] = edge
                if next_id not in expanded:
                    frontier.append((next_id, depth + 1))
        return GraphQueryResult(
            tuple(selected_branches.values()),
            truncated=truncated,
            gaps=coverage_gaps(list(selected_branches.values())),
        )
    paths: list[tuple[str, ...]] = []
    used: dict[str, GraphFact] = {}
    truncated = False
    queue: deque[tuple[str, tuple[str, ...], tuple[str, ...]]] = deque(
        (source, (source,), ()) for source in sources if source in all_ids
    )
    scheduled = len(queue)
    explored = 0
    while queue:
        current, visited, path = queue.popleft()
        explored += 1
        if explored > 5000:
            truncated = True
            break
        if path and (not targets or current in targets):
            paths.append(path)
            if len(paths) >= max_results:
                truncated = bool(queue or adjacency.get(current))
                break
            if current in targets:
                continue
        if len(path) >= max_hops:
            truncated |= any(next_id not in visited for next_id, _ in adjacency.get(current, []))
            continue
        for next_id, edge in adjacency.get(current, []):
            if next_id in visited:
                continue
            if scheduled >= 5000:
                truncated = True
                break
            used[edge.fact_id] = edge
            queue.append((next_id, (*visited, next_id), (*path, edge.fact_id)))
            scheduled += 1
    path_ids = {key for path in paths for key in path}
    selected_paths = [f for key, f in used.items() if key in path_ids]
    return GraphQueryResult(
        tuple(selected_paths), tuple(paths), truncated, coverage_gaps(selected_paths)
    )
