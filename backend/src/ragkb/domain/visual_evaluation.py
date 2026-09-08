"""Independent gold evaluation: unresolved component identity never earns edge credit.

This evaluator deliberately does not import the online graph comparator or its endpoint
serialization. Gold membership and human regions establish identities before edges are
scored, so an incorrect edge cannot manufacture the correspondence that makes it correct.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ragkb.domain.visual_graph import Edge, Graph, LocatedElement
from ragkb.domain.visuals import VisualExtraction


def _literal(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _normal(text: str) -> str:
    # Independent, deliberately narrow typography rule. Do not fold case, signs,
    # comparison operators or all Unicode width variants (which can change units).
    return _literal(text).replace("?", "？")


def _cells(extraction: VisualExtraction | None) -> dict[tuple[int, int, int], str]:
    return {
        (index, row, column): _literal(cell.text)
        for index, table in enumerate(extraction.tables if extraction else [])
        for cell in table.cells
        for row in range(cell.row, cell.row + cell.rowspan)
        for column in range(cell.column, cell.column + cell.colspan)
    }


def _addresses(graph: Graph, *, exact: bool = False) -> dict[str, str]:
    groups = {group.id: group for group in graph.groups}
    normal = _literal if exact else _normal

    def content(key: str) -> tuple[Any, ...]:
        group = groups[key]
        return (
            normal(group.label),
            tuple(sorted((normal(n.label), n.shape) for n in graph.nodes if n.group == key)),
            tuple(sorted(content(g.id) for g in graph.groups if g.parent == key)),
        )

    def ancestors(key: str | None) -> tuple[Any, ...]:
        if key is None:
            return ()
        return (*ancestors(groups[key].parent), content(key))

    return {
        **{g.id: repr(("group", ancestors(g.id))) for g in graph.groups},
        **{n.id: repr(("node", ancestors(n.group), normal(n.label), n.shape)) for n in graph.nodes},
    }


def _region_agrees(gold: LocatedElement, predicted: LocatedElement) -> bool:
    if gold.bbox is None or gold.bbox_basis == "unverified":
        return True
    if predicted.bbox is None:
        return False
    ax, ay, bx, by = gold.bbox
    px, py, qx, qy = predicted.bbox
    intersection = max(0.0, min(bx, qx) - max(ax, px)) * max(0.0, min(by, qy) - max(ay, py))
    union = (bx - ax) * (by - ay) + (qx - px) * (qy - py) - intersection
    return intersection / union >= 0.5


def _identity(gold: Graph, predicted: Graph, *, exact: bool = False) -> tuple[dict[str, str], int]:
    expected, actual = _addresses(gold, exact=exact), _addresses(predicted, exact=exact)
    gold_elements: dict[str, LocatedElement] = {
        **{g.id: g for g in gold.groups},
        **{n.id: n for n in gold.nodes},
    }
    predicted_elements: dict[str, LocatedElement] = {
        **{g.id: g for g in predicted.groups},
        **{n.id: n for n in predicted.nodes},
    }
    expected_counts, actual_counts = Counter(expected.values()), Counter(actual.values())
    candidates: dict[str, list[str]] = {}
    repeated: set[str] = set()
    for key, address in expected.items():
        structural = [other for other in actual if actual[other] == address]
        if expected_counts[address] == actual_counts[address] == 1:
            # A unique printed identity remains correct even when the model omitted its
            # coordinates. Region accuracy is a separate metric, not a content gate.
            candidates[key] = structural
            continue
        repeated.add(key)
        anchor = gold_elements[key]
        candidates[key] = [
            other
            for other in structural
            if anchor.bbox is not None
            and anchor.bbox_basis != "unverified"
            and _region_agrees(anchor, predicted_elements[other])
        ]
    usage = Counter(other for values in candidates.values() for other in values)
    mapping = {
        values[0]: key
        for key, values in candidates.items()
        if len(values) == 1 and usage[values[0]] == 1
    }
    matched = set(mapping.values())
    ambiguous = sum(key not in matched and actual_counts[expected[key]] > 0 for key in repeated)
    return mapping, ambiguous


def _edge_key(
    edge: Edge, identities: dict[str, str], *, exact: bool = False
) -> tuple[str, ...] | None:
    source, target = identities.get(edge.source), identities.get(edge.target)
    if source is None or target is None:
        return None
    if edge.direction in {"both", "none"}:
        source, target = sorted((source, target))
    normal = _literal if exact else _normal
    return source, target, edge.direction, edge.style, normal(edge.label), normal(edge.condition)


def _edge_counts(
    graph: Graph, identities: dict[str, str], *, exact: bool = False
) -> Counter[tuple[str, ...]]:
    result: Counter[tuple[str, ...]] = Counter()
    for edge in graph.edges:
        key = _edge_key(edge, identities, exact=exact)
        if key is not None:
            result[key] += 1
    return result


def _region_details(
    gold: Graph, predicted: Graph | None, mapping: dict[str, str], graph_index: int
) -> list[dict[str, Any]]:
    """Score coordinates against reliable gold, after independent content matching.

    Predicted boxes may be model estimates: IoU measures their accuracy against the
    human/OCR truth and never promotes their provenance to a confirmed source region.
    """
    actual_elements: dict[str, LocatedElement] = (
        {**{g.id: g for g in predicted.groups}, **{n.id: n for n in predicted.nodes}}
        if predicted
        else {}
    )
    inverse = {expected: actual for actual, expected in mapping.items()}
    identity = {**{g.id: g.id for g in gold.groups}, **{n.id: n.id for n in gold.nodes}}
    output: list[dict[str, Any]] = []

    def record(
        kind: str, key: str, expected: LocatedElement, actual: LocatedElement | None
    ) -> None:
        if expected.bbox is None or expected.bbox_basis == "unverified":
            return
        status = (
            "identity_unresolved"
            if actual is None
            else "missing"
            if actual.bbox is None
            else "correct"
            if _region_agrees(expected, actual)
            else "mismatch"
        )
        output.append(
            {
                "graph_index": graph_index,
                "kind": kind,
                "element_id": key,
                "expected_bbox": expected.bbox,
                "predicted_bbox": actual.bbox if actual else None,
                "status": status,
            }
        )

    for kind, elements in (("group", gold.groups), ("node", gold.nodes)):
        for element in elements:
            record(kind, element.id, element, actual_elements.get(inverse.get(element.id, "")))
    expected_edges = _edge_counts(gold, identity)
    for index, edge in enumerate(gold.edges):
        key = _edge_key(edge, identity)
        candidates = (
            [candidate for candidate in predicted.edges if _edge_key(candidate, mapping) == key]
            if predicted
            else []
        )
        actual_edge: Edge | None
        if key is not None and expected_edges[key] == 1 and len(candidates) == 1:
            actual_edge = candidates[0]
        else:
            anchored = [
                candidate
                for candidate in candidates
                if edge.bbox is not None
                and edge.bbox_basis != "unverified"
                and candidate.bbox is not None
                and _region_agrees(edge, candidate)
            ]
            actual_edge = anchored[0] if len(anchored) == 1 else None
        record("edge", f"edge:{index}", edge, actual_edge)
    return output


def evaluate_pair(gold: VisualExtraction, predicted: VisualExtraction | None) -> dict[str, Any]:
    expected_cells, actual_cells = _cells(gold), _cells(predicted)
    nodes_correct = edges_correct = nodes_exact = edges_exact = ambiguous = 0
    regions: list[dict[str, Any]] = []
    for index, graph in enumerate(gold.graphs):
        if predicted is None or index >= len(predicted.graphs):
            regions.extend(_region_details(graph, None, {}, index))
            continue
        candidate = predicted.graphs[index]
        mapping, uncertain = _identity(graph, candidate)
        regions.extend(_region_details(graph, candidate, mapping, index))
        ambiguous += uncertain
        nodes_correct += sum(node.id in mapping for node in candidate.nodes)
        expected_edges = _edge_counts(
            graph, {**{g.id: g.id for g in graph.groups}, **{n.id: n.id for n in graph.nodes}}
        )
        actual_edges = _edge_counts(candidate, mapping)
        edges_correct += sum((expected_edges & actual_edges).values())
        exact_mapping, _ = _identity(graph, candidate, exact=True)
        nodes_exact += sum(node.id in exact_mapping for node in candidate.nodes)
        exact_expected = _edge_counts(
            graph,
            {**{g.id: g.id for g in graph.groups}, **{n.id: n.id for n in graph.nodes}},
            exact=True,
        )
        edges_exact += sum(
            (exact_expected & _edge_counts(candidate, exact_mapping, exact=True)).values()
        )
    return {
        "kind_correct": bool(predicted and predicted.kind == gold.kind),
        "cells_expected": len(expected_cells),
        "cells_predicted": len(actual_cells),
        "cells_correct": sum(
            actual_cells.get(key) == value for key, value in expected_cells.items()
        ),
        "nodes_expected": sum(len(graph.nodes) for graph in gold.graphs),
        "nodes_predicted": sum(len(graph.nodes) for graph in predicted.graphs) if predicted else 0,
        "nodes_correct": nodes_correct,
        "nodes_exact": nodes_exact,
        "edges_expected": sum(len(graph.edges) for graph in gold.graphs),
        "edges_predicted": sum(len(graph.edges) for graph in predicted.graphs) if predicted else 0,
        "edges_correct": edges_correct,
        "edges_exact": edges_exact,
        "normalization_policy": "whitespace-and-fullwidth-question-mark-only",
        "identity_ambiguous": ambiguous,
        "regions_expected": len(regions),
        "regions_predicted": sum(
            element.bbox is not None
            for graph in predicted.graphs
            for element in [*graph.groups, *graph.nodes, *graph.edges]
        )
        if predicted
        else 0,
        "regions_correct": sum(region["status"] == "correct" for region in regions),
        "regions_missing": sum(
            region["status"] in {"missing", "identity_unresolved"} for region in regions
        ),
        "regions_mismatched": sum(region["status"] == "mismatch" for region in regions),
        "region_details": regions,
        "uncertainty_flagged": bool(predicted and predicted.issues()),
    }
