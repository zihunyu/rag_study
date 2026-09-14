"""Recover titles, merged headers and separate table regions without losing source rows."""

import re
import zipfile
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from openpyxl.utils.cell import get_column_letter, range_boundaries

from ragkb.domain.documents import CanonicalNode, SourceLocator

_LABEL = re.compile(
    r"产品|型号|名称|数量|单价|金额|日期|时间|项目|规格|单位|备注|姓名|序号|价格|销售|收入|成本|"
    r"(?i:product|quantity|price|amount|date|name|total|description|unit|cost)"
)


def merged_ranges(path: Path, sheet_path: str) -> list[tuple[int, int, int, int]]:
    result: list[tuple[int, int, int, int]] = []
    with zipfile.ZipFile(path) as archive, archive.open(sheet_path.lstrip("/")) as handle:
        for _, element in ElementTree.iterparse(handle, events=("end",)):  # noqa: S314 -- input already validated; no entity resolver
            if element.tag.rsplit("}", 1)[-1] == "mergeCell":
                left, top, right, bottom = range_boundaries(element.attrib["ref"])
                if left is None or top is None or right is None or bottom is None:
                    raise ValueError("SPREADSHEET_INVALID_MERGE")
                result.append((left, top, right, bottom))
                if len(result) > 10000:
                    raise ValueError("SPREADSHEET_MERGE_LIMIT")
            element.clear()
    return result


def annotate_tables(
    nodes: list[CanonicalNode],
    merges: Sequence[tuple[int, int, int, int]] = (),
) -> tuple[list[CanonicalNode], list[dict[str, Any]]]:
    if not nodes:
        return [], []
    groups: list[list[CanonicalNode]] = [[]]
    for node in nodes:
        values = node.metadata["values"]
        previous = groups[-1][-1] if groups[-1] else None
        new_header = sum(bool(_LABEL.fullmatch(v)) for v in values if v) >= 2
        prior_data = previous and (
            sum(bool(v.strip()) for v in previous.metadata["values"]) >= 2
            and any(re.fullmatch(r"[-+]?\d[\d,.% ]*", v) for v in previous.metadata["values"])
        )
        if previous and (
            node.metadata["row"] > previous.metadata["row"] + 1 or (new_header and prior_data)
        ):
            groups.append([])
        groups[-1].append(node)
    output, tables = [], []
    for index, group in enumerate(groups, 1):
        start = 0
        while start + 1 < len(group) and (
            sum(bool(v.strip()) for v in group[start].metadata["values"]) == 1
            and sum(bool(v.strip()) for v in group[start + 1].metadata["values"]) >= 2
        ):
            start += 1
        header_row = group[start].metadata["row"]
        header_end = max([header_row] + [b for _, t, _, b in merges if t == header_row])
        if header_end - header_row > 7:
            header_end = header_row  # Large merged banners are not a multirow header.
        horizontal = any(top == header_row and right > left for left, top, right, _ in merges)
        if horizontal and start + 1 < len(group):
            following = group[start + 1]
            if sum(bool(_LABEL.search(v)) for v in following.metadata["values"] if v) >= 2:
                header_end = max(header_end, following.metadata["row"])
        headers = [n for n in group if header_row <= n.metadata["row"] <= header_end]
        width = max(len(n.metadata["values"]) for n in group)
        values_by_row = {n.metadata["row"]: n.metadata["values"] for n in group}
        columns = []
        for col in range(1, width + 1):
            labels = []
            for header in headers:
                row = header.metadata["row"]
                source_row, source_col = row, col
                for left, top, right, bottom in merges:
                    if left <= col <= right and top <= row <= bottom:
                        source_row, source_col = top, left
                        break
                source = values_by_row.get(source_row, [])
                label = source[source_col - 1] if source_col <= len(source) else ""
                if label and label not in labels:
                    labels.append(label)
            columns.append(" / ".join(labels))
        title = " / ".join(n.original_text.strip(" |") for n in group[:start])
        sheet = str(group[0].locator.sheet)
        header_locator = SourceLocator(
            sheet=sheet, cell_range=f"A{header_row}:{get_column_letter(width)}{header_end}"
        ).to_dict()
        table = {
            "sheet": sheet,
            "table_id": f"{sheet}:{index}",
            "title": title,
            "header_rows": [n.metadata["row"] for n in headers],
            "columns": columns,
            "non_empty_rows": len(group),
            "header_basis": "merged_layout" if merges else "row_layout",
        }
        tables.append(table)
        for node in group:
            row = node.metadata["row"]
            output.append(
                replace(
                    node,
                    metadata={
                        **node.metadata,
                        "table_header": " | ".join(columns),
                        "table_title": title,
                        "table_id": table["table_id"],
                        "header_rows": table["header_rows"],
                        "column_labels": columns,
                        "row_role": "title"
                        if row < header_row
                        else "header"
                        if row <= header_end
                        else "data",
                        "source_spans": [
                            {"locator": node.locator.to_dict(), "role": "table_context"},
                            {"locator": header_locator, "role": "table_header"},
                        ],
                    },
                )
            )
    return output, tables
