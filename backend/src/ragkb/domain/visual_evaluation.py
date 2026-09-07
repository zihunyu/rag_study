"""Exact field evaluation against human-reviewed gold, with explicit sample counts."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ragkb.domain.visual_comparison import _edges, _grid
from ragkb.domain.visuals import VisualExtraction


def evaluate_pair(gold: VisualExtraction, predicted: VisualExtraction | None) -> dict[str, Any]:
    expected_cells = {
        (ti, r, c): value
        for ti, table in enumerate(gold.tables)
        for (r, c), value in _grid(table).items()
    }
    actual_cells = {
        (ti, r, c): value
        for ti, table in enumerate(predicted.tables if predicted else [])
        for (r, c), value in _grid(table).items()
    }
    expected_edges, actual_edges = _edges(gold), _edges(predicted) if predicted else Counter()
    expected_nodes = Counter(n.label for g in gold.graphs for n in g.nodes)
    actual_nodes = (
        Counter(n.label for g in predicted.graphs for n in g.nodes) if predicted else Counter()
    )
    return {
        "kind_correct": bool(predicted and predicted.kind == gold.kind),
        "cells_expected": len(expected_cells),
        "cells_predicted": len(actual_cells),
        "cells_correct": sum(
            actual_cells.get(key) == value for key, value in expected_cells.items()
        ),
        "nodes_expected": sum(expected_nodes.values()),
        "nodes_predicted": sum(actual_nodes.values()),
        "nodes_correct": sum((expected_nodes & actual_nodes).values()),
        "edges_expected": sum(expected_edges.values()),
        "edges_predicted": sum(actual_edges.values()),
        "edges_correct": sum((expected_edges & actual_edges).values()),
        "uncertainty_flagged": bool(predicted and predicted.issues()),
    }
