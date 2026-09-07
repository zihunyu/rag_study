"""Bind extracted cells to measured OCR boxes using the complete table topology."""

from __future__ import annotations

from typing import Any

from ragkb.domain.visual_numbers import compact, critical_tokens
from ragkb.domain.visuals import VisualCell, VisualTable


def _compatible(a: VisualCell, x: list[float], b: VisualCell, y: list[float]) -> bool:
    # Ordering is independent of spacing, font size and page position. Merged cells
    # constrain order only beyond their spans, so their labels need not be centred.
    if a.row + a.rowspan <= b.row and (x[1] + x[3]) >= (y[1] + y[3]):
        return False
    if b.row + b.rowspan <= a.row and (y[1] + y[3]) >= (x[1] + x[3]):
        return False
    if a.column + a.colspan <= b.column and (x[0] + x[2]) >= (y[0] + y[2]):
        return False
    if b.column + b.colspan <= a.column and (y[0] + y[2]) >= (x[0] + x[2]):
        return False
    if a.row == b.row and a.rowspan == b.rowspan == 1:
        if min(x[3], y[3]) <= max(x[1], y[1]):
            return False
    if a.column == b.column and a.colspan == b.colspan == 1:
        if min(x[2], y[2]) <= max(x[0], y[0]):
            return False
    return True


def bind_table(table: VisualTable, regions: list[dict[str, Any]], index: int) -> dict[str, Any]:
    cells = [c for c in table.cells if c.text.strip()]
    if not any(critical_tokens(c.text) for c in cells):
        return {"status": "not_required", "targets": [], "issues": []}
    measured = [
        r
        for r in regions
        if r.get("score", 0) >= 0.9
        and len(r.get("bbox", [])) == 4
        and 0 <= r["bbox"][0] < r["bbox"][2] <= 1
        and 0 <= r["bbox"][1] < r["bbox"][3] <= 1
    ]
    candidates = [[r for r in measured if compact(r["text"]) == compact(c.text)] for c in cells]
    label = f"表格 {index + 1}"
    if any(not values or len(values) > 24 for values in candidates) or len(cells) > 400:
        return {
            "status": "inconclusive",
            "targets": [],
            "issues": [label + "：无法独立定位全部行列标签、数值和单位，请对照原图复核"],
        }
    solutions: list[dict[int, dict[str, Any]]] = []
    steps = 0
    exhausted = False

    def search(chosen: dict[int, dict[str, Any]], remaining: list[int]) -> None:
        nonlocal steps, exhausted
        steps += 1
        if steps > 5000:
            exhausted = True
            return
        if len(solutions) >= 2:
            return
        if not remaining:
            solutions.append(dict(chosen))
            return
        filtered = {
            i: [
                r
                for r in candidates[i]
                if all(
                    r["id"] != other["id"]
                    and _compatible(cells[i], r["bbox"], cells[j], other["bbox"])
                    for j, other in chosen.items()
                )
            ]
            for i in remaining
        }
        i = min(remaining, key=lambda k: len(filtered[k]))
        for region in filtered[i]:
            search({**chosen, i: region}, [j for j in remaining if j != i])
            if exhausted or len(solutions) >= 2:
                break

    search({}, list(range(len(cells))))
    if not solutions and not exhausted:
        return {
            "status": "disagreement",
            "targets": [],
            "issues": [label + "：识别数值与原图行列、产品名或表头位置不一致"],
        }
    if len(solutions) != 1 or exhausted:
        return {
            "status": "inconclusive",
            "targets": [],
            "issues": [label + "：存在多个可能的表格位置，无法唯一确认数值归属"],
        }
    return {
        "status": "consistent",
        "issues": [],
        "targets": [
            {
                "kind": "cell",
                "table": index,
                "row": cell.row,
                "column": cell.column,
                "region_ids": [solutions[0][i]["id"]],
                "basis": "ocr_table_topology",
            }
            for i, cell in enumerate(cells)
        ],
    }
