"""Minimal answer reference presentation; management locators stay behind management APIs."""

from __future__ import annotations

from typing import Any


def citation_locator(locator: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in locator.items()
        if key
        in {
            "page",
            "page_number",
            "page_start",
            "page_end",
            "paragraph",
            "paragraph_index",
            "start_line",
            "end_line",
            "section_path",
            "heading_path",
            "heading",
            "sheet",
            "sheet_name",
            "slide",
            "slide_number",
            "row_start",
            "row_end",
            "cell_range",
            "filename",
            "caption",
            "document_name",
            "visual_asset_ids",
            "source_type",
        }
    }


def cited_asset_ids(locator: dict[str, Any]) -> list[str]:
    ids = [i for i in locator.get("visual_asset_ids", []) if isinstance(i, str)]
    if "used_visual_fact_ids" not in locator:
        return ids  # Older runs attach only the cited chunk's original source images.
    receipt = locator.get("used_visual_fact_ids", [])
    if not isinstance(receipt, list):
        return []
    return [
        asset
        for asset in ids
        if any(isinstance(fact, str) and f":{asset}:" in fact for fact in receipt)
    ]


def reader_report(report: dict[str, Any]) -> dict[str, Any]:
    """An execution report must not become an uncited document/section listing."""
    value: dict[str, Any] = {
        k: v
        for k, v in report.items()
        if k
        in {
            "mode",
            "complete",
            "total_sections",
            "read_sections",
            "read_chunks",
            "checked_images",
            "answer_sections",
            "total_chunks",
        }
        and isinstance(v, (str, int, float, bool))
    }
    if isinstance(report.get("conditions"), dict):
        value["conditions"] = {
            k: v
            for k, v in report["conditions"].items()
            if k in {"checked", "covered", "missing", "not_applicable", "complete"}
            and isinstance(v, (int, bool))
        }
    value["image_checks"] = [
        {"status": c["status"]}
        for c in report.get("image_checks", [])
        if isinstance(c, dict)
        and c.get("status")
        in {
            "supported",
            "uncertain",
            "conflict",
            "verification_failed",
            "irrelevant",
            "budget_exceeded",
        }
    ]
    if report.get("gaps"):
        value["gaps"] = ["本轮尚有未覆盖的资料或条件，请缩小问题范围后重试。"]
    return value
