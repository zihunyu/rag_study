"""Complete image-table rows with repeated multirow headers and source coordinates."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ragkb.contracts.ports import ParsingDeferred
from ragkb.domain.documents import CanonicalNode
from ragkb.domain.visuals import VisualTable

if TYPE_CHECKING:
    from ragkb.document_processing.chunking import ChunkingConfig, TokenizerPort


def _cell(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", " / ")
    )


def table_windows(
    node: CanonicalNode, config: ChunkingConfig, tokenizer: TokenizerPort
) -> list[tuple[str, int, int, dict[str, Any]]]:
    table = VisualTable.model_validate(node.metadata["visual_table"])
    grid = [["" for _ in range(table.columns)] for _ in range(table.rows)]
    for cell in table.cells:
        for row in range(cell.row, cell.row + cell.rowspan):
            for column in range(cell.column, cell.column + cell.colspan):
                grid[row][column] = cell.text
    headers = [
        " / ".join(dict.fromkeys(grid[r][c] for r in range(table.header_rows) if grid[r][c]))
        or f"第 {c + 1} 列（原图未标表头）"
        for c in range(table.columns)
    ]
    context = "\n".join(
        dict.fromkeys(
            filter(
                None,
                [
                    str(node.metadata.get("table_context", "")),
                    table.title,
                    *("限定条件 / 注释：" + note for note in table.notes),
                ],
            )
        )
    )
    header = "| " + " | ".join(_cell(value) for value in headers) + " |\n"
    header += "| " + " | ".join("---" for _ in headers) + " |"

    def render(rows: list[int]) -> str:
        body = ["| " + " | ".join(_cell(value) for value in grid[row]) + " |" for row in rows]
        return "\n\n".join(filter(None, [context, "\n".join([header, *body])]))

    windows: list[tuple[str, int, int, dict[str, Any]]] = []
    selected: list[int] = []

    def flush() -> None:
        if not selected:
            return
        text = render(selected)
        windows.append(
            (
                text,
                0,
                len(node.original_text),
                {
                    "visual_table_row_range": [selected[0] + 1, selected[-1] + 1],
                    "visual_table_header_rows": table.header_rows,
                    "source_spans": [
                        {
                            "locator": node.locator.to_dict(),
                            "role": "visual_table_rows",
                            "table_index": node.metadata["visual_table_index"],
                            "table_row_range": [selected[0] + 1, selected[-1] + 1],
                            "header_row_range": [1, table.header_rows] if table.header_rows else [],
                        }
                    ],
                },
            )
        )
        selected.clear()

    for row in range(table.header_rows, table.rows):
        if len(tokenizer.spans(render([row]))) > config.max_tokens:
            # Splitting a cell destroys the association with its column and units.
            raise ParsingDeferred(
                "VISUAL_TABLE_ROW_TOO_LARGE",
                "完整表头、注释和单行超过分块上限，请调整表格或分块预算",
            )
        if selected and len(tokenizer.spans(render([*selected, row]))) > config.target_tokens:
            flush()
        selected.append(row)
    flush()
    if not windows:
        text = render([])
        if len(tokenizer.spans(text)) > config.max_tokens:
            raise ParsingDeferred("VISUAL_TABLE_ROW_TOO_LARGE", "完整表头超过分块预算")
        windows.append((text, 0, len(node.original_text), {}))
    return windows
