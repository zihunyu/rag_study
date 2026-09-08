"""Typed visual extraction: source transcription, tables and relationships stay distinct."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, model_validator

from ragkb.domain.graph_facts import graph_facts
from ragkb.domain.visual_graph import Graph, StrictModel, validate_graph


class VisualCell(StrictModel):
    row: int = Field(ge=0, le=1000)
    column: int = Field(ge=0, le=1000)
    rowspan: int = Field(ge=1, le=1000)
    colspan: int = Field(ge=1, le=1000)
    text: str = Field(max_length=10000)


class VisualTable(StrictModel):
    title: str = Field(max_length=1000)
    rows: int = Field(ge=1, le=1000)
    columns: int = Field(ge=1, le=1000)
    cells: list[VisualCell] = Field(max_length=10000)
    header_rows: int = Field(default=0, ge=0, le=1000)
    notes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_grid(self) -> VisualTable:
        if self.header_rows > self.rows:
            raise ValueError("VISUAL_TABLE_HEADER_OUTSIDE_GRID")
        if self.rows * self.columns > 10000:
            raise ValueError("VISUAL_TABLE_TOO_LARGE")
        occupied: set[tuple[int, int]] = set()
        for cell in self.cells:
            if cell.row + cell.rowspan > self.rows or cell.column + cell.colspan > self.columns:
                raise ValueError("VISUAL_TABLE_CELL_OUTSIDE_GRID")
            for row in range(cell.row, cell.row + cell.rowspan):
                for column in range(cell.column, cell.column + cell.colspan):
                    if (row, column) in occupied:
                        raise ValueError("VISUAL_TABLE_OVERLAPPING_CELLS")
                    occupied.add((row, column))
        if len(occupied) != self.rows * self.columns:
            raise ValueError("VISUAL_TABLE_MISSING_CELLS")
        return self

    def as_html(self) -> str:
        rows = []
        for row in range(self.rows):
            cells = sorted((c for c in self.cells if c.row == row), key=lambda c: c.column)
            rows.append(
                "<tr>"
                + "".join(
                    f'<td rowspan="{c.rowspan}" colspan="{c.colspan}">{html.escape(c.text)}</td>'
                    for c in cells
                )
                + "</tr>"
            )
        return "<table>" + "".join(rows) + "</table>"


class VisualExtraction(StrictModel):
    kind: Literal["diagram", "table", "photo", "chart", "text", "mixed", "unknown"]
    title: str = Field(max_length=1000)
    transcription: str = Field(max_length=80000)
    description: str = Field(max_length=16000)
    graphs: list[Graph] = Field(max_length=12)
    tables: list[VisualTable] = Field(max_length=12)
    uncertainties: list[str] = Field(max_length=100)
    body_text: str = Field(default="", max_length=80000)

    @model_validator(mode="after")
    def validate_semantics(self) -> VisualExtraction:
        if self.kind == "diagram" and not self.graphs:
            raise ValueError("VISUAL_DIAGRAM_GRAPH_MISSING")
        if self.kind == "table" and not self.tables:
            raise ValueError("VISUAL_TABLE_GRID_MISSING")
        if self.graphs and self.kind not in {"diagram", "mixed"}:
            raise ValueError("VISUAL_GRAPH_KIND_MISMATCH")
        for graph in self.graphs:
            if len(graph.nodes) + len(graph.groups) > 400 or len(graph.edges) > 1200:
                raise ValueError("VISUAL_GRAPH_TOO_LARGE")
            validate_graph(graph)
        return self

    def issues(self) -> list[str]:
        return list(
            dict.fromkeys(
                self.uncertainties
                + [issue for graph in self.graphs for issue in graph.uncertainties]
                + (["图片类型尚不明确"] if self.kind == "unknown" else [])
            )
        )

    def retrieval_text(
        self, *, include_tables: bool = True, include_transcription: bool = True
    ) -> str:
        parts = [self.title, self.description]
        if self.body_text:
            parts.append(self.body_text)
        blocked_graph_facts = any(
            graph.uncertainties
            or any(
                item.review_status in {"pending", "excluded"}
                for item in [*graph.groups, *graph.nodes, *graph.edges]
            )
            for graph in self.graphs
        )
        if blocked_graph_facts:
            # Generated prose may still contain excluded relationships. Structured facts
            # are authoritative after partial review; the original remains in the audit.
            parts = [self.title]
        if include_transcription and not blocked_graph_facts:
            parts.append(self.transcription)
        for index, graph in enumerate(self.graphs):
            parts.extend(
                fact.text for fact in graph_facts(graph, scope=f"text:{index}", verified=True)
            )
        if include_tables:
            parts.extend(
                "\n".join([table.title, table.as_html(), *table.notes]) for table in self.tables
            )
        return "\n\n".join(dict.fromkeys(p.strip() for p in parts if p.strip()))


def reviewed_extraction(original: VisualExtraction, edited: VisualExtraction) -> VisualExtraction:
    """Do not retain stale model prose or transcription after a structural correction.

    Original output stays in the base version. Text outside a table/graph belongs
    in body_text; the human reviewer explicitly confirms that field separately.
    """
    if any(
        graph.uncertainties
        or any(
            item.review_status in {"pending", "excluded"}
            for item in [*graph.groups, *graph.nodes, *graph.edges]
        )
        for graph in edited.graphs
    ):
        return edited.model_copy(update={"transcription": "", "description": "", "body_text": ""})
    if original.tables == edited.tables and original.graphs == edited.graphs:
        return edited
    visible = [edited.body_text]
    for table in edited.tables:
        visible.extend(
            [
                table.title,
                *(c.text for c in sorted(table.cells, key=lambda c: (c.row, c.column))),
                *table.notes,
            ]
        )
    for graph in edited.graphs:
        visible.extend(
            [
                *(n.label for n in graph.nodes),
                *(g.label for g in graph.groups),
                *(e.label for e in graph.edges),
            ]
        )
    return edited.model_copy(
        update={
            "transcription": "\n".join(dict.fromkeys(t for t in visible if t)),
            "description": "" if edited.description == original.description else edited.description,
        }
    )


class VisualVerification(StrictModel):
    kind_correct: bool
    text_correct: bool
    structure_correct: bool
    complete: bool
    issues: list[str] = Field(max_length=100)

    def passed(self) -> bool:
        return (
            all((self.kind_correct, self.text_correct, self.structure_correct, self.complete))
            and not self.issues
        )


class VisualQueryResult(StrictModel):
    status: Literal["supported", "not_relevant", "uncertain", "conflict"]
    text: str = Field(max_length=10000)
    uncertainties: list[str] = Field(max_length=100)
    unanswered_topics: list[str] = Field(default_factory=list, max_length=30)


@dataclass(frozen=True)
class VisualQueryOutcome:
    status: Literal[
        "supported",
        "not_relevant",
        "uncertain",
        "conflict",
        "verification_failed",
        "budget_exceeded",
    ]
    text: str = ""
    issues: tuple[str, ...] = ()
    unanswered_topics: tuple[str, ...] = ()
    facts: tuple[dict[str, Any], ...] = ()
    truncated: bool = False
