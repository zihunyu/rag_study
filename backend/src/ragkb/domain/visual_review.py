"""Issue receipts and conservative graph exclusion for a human-reviewed source image."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ragkb.domain.visuals import VisualExtraction

INFORMATION_CODES = frozenset(
    {
        "VISUAL_METADATA_TITLE_INFERRED",
        "VISUAL_EDGE_LABEL_ABSENT",
        "VISUAL_TRANSCRIPTION_VISIBLE_SPELLING",
    }
)
BLOCKING_CODES = frozenset(
    {
        "OCR_STRUCTURE_INVALID",
        "VISUAL_GRAPH_INVALID_SOURCE_REGION",
        "VISUAL_TABLE_OVERLAPPING_CELLS",
        "VISUAL_TABLE_MISSING_CELLS",
        "VISUAL_TABLE_CELL_OUTSIDE_GRID",
        "VISUAL_GRAPH_KIND_MISMATCH",
    }
)


def issue_severity(asset: dict[str, Any], message: str) -> str:
    """Only explicit machine metadata codes are informational, never prose guesses."""
    if message in INFORMATION_CODES:
        return "info"
    local = asset.get("local_check") or {}
    if message in BLOCKING_CODES or asset.get("status") == "failed":
        return "blocking"
    if local.get("status") == "disagreement" and message in local.get("issues", []):
        return "blocking"
    if (asset.get("extraction") or {}).get("kind") == "unknown" and message == "图片类型尚不明确":
        return "blocking"
    return "review"


class IssueResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    disposition: Literal["corrected", "confirmed", "excluded"]
    reason: str = Field(min_length=2, max_length=2000, pattern=r".*\S.*")
    targets: list[str] = Field(default_factory=list, max_length=200)


def review_issues(asset: dict[str, Any]) -> list[dict[str, str]]:
    extraction = asset.get("extraction") or {}
    scoped = [("extraction", message) for message in extraction.get("uncertainties", [])]
    scoped += [
        (f"graphs/{index}", message)
        for index, graph in enumerate(extraction.get("graphs", []))
        for message in graph.get("uncertainties", [])
    ]
    known = {str(message).strip() for _, message in scoped}
    asset_scope = {"diagram": "graphs", "table": "tables"}.get(str(extraction.get("kind")), "asset")
    scoped = [
        (asset_scope, message)
        for message in asset.get("issues") or []
        if str(message).strip() not in known
    ] + scoped
    if extraction.get("kind") == "unknown":
        scoped.append(("extraction", "图片类型尚不明确"))
    return [
        {
            "id": hashlib.sha256(
                (asset["id"] + "\n" + scope + "\n" + message).encode()
            ).hexdigest()[:24],
            "message": message,
            "scope": scope,
            "severity": issue_severity(asset, message),
        }
        for scope, message in dict.fromkeys(
            (scope, str(m).strip()) for scope, m in scoped if str(m).strip()
        )
    ]


def review_targets(extraction: VisualExtraction) -> dict[str, dict[str, Any]]:
    value = extraction.model_dump()
    result: dict[str, dict[str, Any]] = {
        key: {"value": value[key]}
        for key in (
            "kind",
            "title",
            "description",
            "transcription",
            "body_text",
            "graphs",
            "tables",
        )
    }
    for gi, graph in enumerate(extraction.graphs):
        result[f"graphs/{gi}"] = graph.model_dump()
        for kind in ("groups", "nodes", "edges"):
            for index, item in enumerate(getattr(graph, kind)):
                result[f"graphs/{gi}/{kind}/{index}"] = item.model_dump()
    for ti, table in enumerate(extraction.tables):
        result[f"tables/{ti}"] = table.model_dump()
        for key in ("title", "notes", "header_rows"):
            result[f"tables/{ti}/{key}"] = {"value": getattr(table, key)}
        for ci, cell in enumerate(table.cells):
            result[f"tables/{ti}/cells/{ci}"] = cell.model_dump()
    return result


def substantive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: substantive(item)
            for key, item in value.items()
            if key not in {"uncertainties", "review_status", "bbox_basis", "bbox"}
        }
    if isinstance(value, list):
        return [substantive(item) for item in value]
    return value


def validate_review(
    asset: dict[str, Any], edited: VisualExtraction, resolutions: list[IssueResolution]
) -> None:
    issues = {i["id"]: i for i in review_issues(asset) if i["severity"] != "info"}
    expected = set(issues)
    received = [r.issue_id for r in resolutions]
    if len(received) != len(set(received)) or set(received) != expected:
        raise ValueError("VISUAL_ISSUE_RESOLUTIONS_REQUIRED")
    original = (
        VisualExtraction.model_validate(asset["extraction"]).model_dump()
        if asset.get("extraction")
        else {}
    )
    original_targets = review_targets(VisualExtraction.model_validate(original)) if original else {}
    targets = review_targets(edited)
    for resolution in resolutions:
        if any(t not in targets for t in resolution.targets):
            raise ValueError("VISUAL_REVIEW_TARGET_INVALID")
        if resolution.disposition == "corrected":
            if not resolution.targets or any(
                substantive(original_targets.get(t)) == substantive(targets[t])
                for t in resolution.targets
            ):
                raise ValueError("VISUAL_REVIEW_CORRECTION_MISSING")
            scope = issues[resolution.issue_id]["scope"]
            if scope.startswith(("graphs", "tables")) and not any(
                t == scope
                or t.startswith(scope + "/")
                or (
                    t == scope.split("/")[0] and len(original.get(t, [])) != len(getattr(edited, t))
                )
                for t in resolution.targets
            ):
                raise ValueError("VISUAL_REVIEW_CORRECTION_SCOPE_MISMATCH")
        if resolution.disposition == "excluded" and (
            not resolution.targets
            or any(targets[t].get("review_status") != "excluded" for t in resolution.targets)
        ):
            raise ValueError("VISUAL_REVIEW_EXCLUSION_MISSING")
    for graph in edited.graphs:
        excluded = {g.id for g in graph.groups if g.review_status == "excluded"}
        excluded.update(n.id for n in graph.nodes if n.review_status == "excluded")
        for group in graph.groups:
            if group.parent in excluded and group.id not in excluded:
                raise ValueError("VISUAL_EXCLUDED_GROUP_DEPENDENCY")
        for node in graph.nodes:
            if node.group in excluded and node.id not in excluded:
                raise ValueError("VISUAL_EXCLUDED_GROUP_DEPENDENCY")
        for edge in graph.edges:
            if (edge.source in excluded or edge.target in excluded) and getattr(
                edge, "review_status", "inherited"
            ) != "excluded":
                raise ValueError("VISUAL_EXCLUDED_EDGE_DEPENDENCY")
        if any(
            getattr(x, "review_status", "inherited") == "pending"
            for x in [*graph.groups, *graph.nodes, *graph.edges]
        ):
            raise ValueError("VISUAL_UNRESOLVED_UNCERTAINTIES")
