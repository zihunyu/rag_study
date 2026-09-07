"""Specific disagreement checks for independent readings and human revision diffs."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ragkb.domain.visuals import VisualExtraction


def _text(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _grid(table: Any) -> dict[tuple[int, int], str]:
    return {
        (r, c): _text(cell.text)
        for cell in table.cells
        for r in range(cell.row, cell.row + cell.rowspan)
        for c in range(cell.column, cell.column + cell.colspan)
    }


def _edges(extraction: VisualExtraction) -> Counter[tuple[str, str, str, str]]:
    result: Counter[tuple[str, str, str, str]] = Counter()
    for graph in extraction.graphs:
        labels = {node.id: _text(node.label) for node in graph.nodes}
        labels.update({group.id: _text(group.label) for group in graph.groups})
        for edge in graph.edges:
            left, right = labels[edge.source], labels[edge.target]
            if edge.direction in {"both", "none"}:
                left, right = sorted([left, right])
            result[(left, right, edge.direction + ":" + edge.style, _text(edge.label))] += 1
    return result


def compare_readings(first: VisualExtraction, second: VisualExtraction) -> list[str]:
    issues = second.issues()
    if first.kind != second.kind:
        issues.append("两次独立读取对图片类型判断不同")
    if len(first.tables) != len(second.tables):
        issues.append("两次独立读取的表格数量不同")
    for index, (a, b) in enumerate(zip(first.tables, second.tables, strict=False), 1):
        if (a.rows, a.columns, a.header_rows) != (b.rows, b.columns, b.header_rows):
            issues.append(f"表格 {index} 的行列或表头范围存在分歧")
        grid_a, grid_b = _grid(a), _grid(b)
        for row, column in sorted(set(grid_a) | set(grid_b)):
            if grid_a.get((row, column)) != grid_b.get((row, column)):
                issues.append(
                    f"表格 {index} 第 {row + 1} 行第 {column + 1} 列文字、数值或单位存在分歧"
                )
        # Conditions may be worded differently; compare explicit values/units rather than prose.
        for token in re.findall(r"[-+±]?\d+(?:\.\d+)?\s*[A-Za-z℃%°]*", " ".join(a.notes)):
            if (
                re.search(
                    r"(?<![\d.,])" + re.escape(_text(token)) + r"(?![\d.,A-Za-z])",
                    _text(" ".join(b.notes) + "\n" + second.transcription),
                )
                is None
            ):
                issues.append(f"表格 {index} 的限定条件未得到独立确认")
                break
    nodes_a = Counter(_text(n.label) for g in first.graphs for n in g.nodes)
    nodes_b = Counter(_text(n.label) for g in second.graphs for n in g.nodes)
    if nodes_a != nodes_b:
        issues.append("关系图的节点名称或数量存在分歧")
    if any(count > 1 for count in nodes_a.values()):
        issues.append("存在同名节点，需人工核对其分组和连线身份")

    def groups(extraction: VisualExtraction) -> Counter[tuple[str, str, tuple[str, ...]]]:
        return Counter(
            (
                _text(group.label),
                next(
                    (_text(parent.label) for parent in graph.groups if parent.id == group.parent),
                    "",
                ),
                tuple(sorted(_text(node.label) for node in graph.nodes if node.group == group.id)),
            )
            for graph in extraction.graphs
            for group in graph.groups
        )

    if groups(first) != groups(second):
        issues.append("关系图的分组包含关系存在分歧")
    if _edges(first) != _edges(second):
        issues.append("关系图的连线端点、箭头方向或条件存在分歧")
    return list(dict.fromkeys(issues))[:100]


def extraction_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []

    def visit(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(a.keys() | b.keys()):
                visit(a.get(key), b.get(key), f"{path}/{key}")
        elif isinstance(a, list) and isinstance(b, list):
            for index in range(max(len(a), len(b))):
                visit(
                    a[index] if index < len(a) else None,
                    b[index] if index < len(b) else None,
                    f"{path}/{index}",
                )
        elif a != b:
            label = path
            parts = path.strip("/").split("/")
            try:
                if parts[0] == "tables" and parts[2] == "cells":
                    table, cell = int(parts[1]), int(parts[3])
                    source = (after or before)["tables"][table]["cells"][cell]
                    label = (
                        f"表格 {table + 1} · 第 {source['row'] + 1} 行第 {source['column'] + 1} 列"
                    )
                elif parts[0] == "graphs":
                    kind = {"nodes": "节点", "edges": "连线", "groups": "分组"}.get(
                        parts[2], "结构"
                    )
                    field = {
                        "label": "文字",
                        "source": "起点",
                        "target": "终点",
                        "direction": "方向",
                        "style": "线型",
                        "group": "所属分组",
                    }.get(parts[-1], "内容")
                    label = f"关系图 {int(parts[1]) + 1} · {kind} {int(parts[3]) + 1} · {field}"
                else:
                    label = {
                        "description": "可见内容说明",
                        "body_text": "正文",
                        "title": "标题",
                        "transcription": "转录文字",
                        "uncertainties": "不确定内容",
                    }.get(parts[0], "结构与条件")
            except (KeyError, IndexError, ValueError, TypeError):
                label = "结构与条件"
            changes.append({"path": path, "label": label, "before": a, "after": b})

    visit(before, after, "")
    return changes
