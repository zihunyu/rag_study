"""Explain source presence by stage, without equating lexical matches with correctness."""

from __future__ import annotations

from typing import Any

from ragkb.domain.acceptance_points import criteria_for
from ragkb.domain.parsing_acceptance import find_quote, locations

STAGES = ("original", "parsed", "chunks", "retrieval", "model_input", "draft", "final")


class AcceptanceLineage:
    def __init__(self, service: Any) -> None:
        self.service = service

    def original(self, subject: Any, space: str, point: dict[str, Any]) -> dict[str, Any] | None:
        identity, check_id = point.get("original_standard_id"), point.get("original_check_id")
        if not identity and not check_id:
            return None
        if not identity or not check_id:
            raise ValueError("ORIGINAL_STANDARD_AND_CHECK_REQUIRED")
        standard = self.service.parsing.record(subject, space, identity)
        if standard["kind"] != "standard" or not standard["payload"]["original_checked"]:
            raise ValueError("ORIGINAL_STANDARD_NOT_CONFIRMED")
        spec = standard["payload"]
        check = next((c for c in spec["checks"] if c["id"] == check_id), None)
        if not check or spec["document_id"] not in {s["document_id"] for s in point["sources"]}:
            raise ValueError("ORIGINAL_CHECK_SOURCE_MISMATCH")
        for source in point["sources"]:
            if source["document_id"] == spec["document_id"]:
                version = self.service.parsing.version(subject, space, source["version_id"])
                if version["content_sha256"] != spec["original_sha256"]:
                    raise ValueError("ORIGINAL_CHANGED_RECONFIRM_STANDARD")
        return {
            **check,
            "document_id": spec["document_id"],
            "standard_id": identity,
            "revision": standard["revision"],
        }

    def report(
        self, subject: Any, space: str, case: dict[str, Any], step: dict[str, Any]
    ) -> dict[str, Any]:
        points = [p for p in criteria_for(case) if p["kind"] != "relevance"]
        trace = step.get("diagnostics", {}).get("content_trace", {})
        cache: dict[str, Any] = {}
        result = []
        for point in points:
            bound = self.service.assistance.bindings(subject, point["sources"])
            original = self.original(subject, space, point)
            targets = (
                [{"document_id": original["document_id"], "quote": original["quote"]}]
                + [s for s in bound if s["document_id"] != original["document_id"]]
                if original
                else bound
            )
            stages: dict[str, Any] = {
                "original": {
                    "status": "confirmed" if original else "unassessed",
                    "note": "已关联人工对照原文件的标准"
                    if original
                    else "依据来自解析片段，尚未独立核对原文件",
                }
            }
            source_rows: dict[str, list[dict[str, Any]] | None] = {"parsed": [], "chunks": []}
            for source in bound:
                identity = source["version_id"]
                if identity not in cache:
                    document_space = self.service.runtime.repository.get_document_space(
                        source["document_id"]
                    )
                    # Read only the source pages used by this case, once per version.
                    all_bindings = self.service.assistance.bindings(
                        subject,
                        [s for p in points for s in p["sources"] if s["version_id"] == identity],
                    )
                    pages = set().union(*(locations(s) for s in all_bindings))
                    for p in points:
                        ref = self.original(subject, space, p)
                        if ref and ref["document_id"] == source["document_id"]:
                            pages.update(ref["pages"])
                    cache[identity] = self.service.parsing.snapshot(
                        subject, document_space, identity, pages
                    )
                for stage in source_rows:
                    rows = cache[identity][stage]
                    if rows is None:
                        source_rows[stage] = None
                    elif (collected := source_rows[stage]) is not None:
                        collected.extend({**r, "document_id": source["document_id"]} for r in rows)
            for stage in ("parsed", "chunks", "retrieval", "model_input", "draft", "final"):
                if stage in source_rows:
                    rows = source_rows[stage]
                elif stage == "final":
                    rows = [{"text": step["result"].get("answer") or ""}]
                else:
                    captured = trace.get(stage, {})
                    rows = captured.get("rows") if captured.get("recorded") else None
                matches = []
                for target in targets:
                    available = [
                        r
                        for r in rows or []
                        if stage in {"draft", "final"}
                        or r.get("document_id") == target["document_id"]
                    ]
                    found = find_quote(target["quote"], available)
                    matches.append(
                        {
                            "found": bool(found),
                            "source_quote": target["quote"],
                            "locations": [
                                {"id": r.get("id"), "locator": r.get("locator", {})}
                                for r in found[:3]
                            ],
                        }
                    )
                stages[stage] = {
                    "status": "unrecorded"
                    if rows is None
                    else "unassessed"
                    if not targets
                    else "found"
                    if all(m["found"] for m in matches)
                    else "not_found",
                    "matches": matches,
                    "mode": trace.get(stage, {}).get("mode", ""),
                }
            result.append(
                {
                    "point_id": point["id"],
                    "text": point["text"],
                    "stages": stages,
                    "original": original,
                }
            )
        return {
            "revision": "content-lineage-v1",
            "rows": result,
            "method": "exact_original_presence_with_whitespace_normalization",
            "note": "原句存在不等于语义正确；未找到可能是改写，结合逐项评审确认。"
            "模型输入对应首次生成及初稿，未记录时不从检索结果推测。"
            + (
                f"此后另有 {trace['repair_input_calls']} 次生成修复调用。"
                if trace.get("repair_input_calls")
                else ""
            ),
        }


def overlay_review(report: dict[str, Any], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {r["point_id"]: r for r in reviews}
    for row in report.get("rows", []):
        final = row["stages"]["final"]
        review = by_id.get(row["point_id"], {})
        final["semantic_status"] = review.get("status", "pending_review")
        final["semantic_origin"] = review.get("origin", "pending")
        final["semantic_note"] = review.get("note", "")
        row["first_gap"] = next(
            (s for s in STAGES[1:-1] if row["stages"][s]["status"] in {"not_found", "unrecorded"}),
            "final" if review.get("status") in {"missing", "incorrect"} else "",
        )
    return report


def compare_lineage(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    old = {r["point_id"]: r for r in before.get("rows", [])}
    result = []
    for row in after.get("rows", []):
        previous = old.get(row["point_id"])
        if not previous:
            continue
        for stage in STAGES:
            a, b = previous["stages"][stage]["status"], row["stages"][stage]["status"]
            if a != b:
                result.append(
                    {
                        "point_id": row["point_id"],
                        "text": row["text"],
                        "stage": stage,
                        "before": a,
                        "after": b,
                        "change": "pending"
                        if any(s in {"unrecorded", "unassessed"} for s in (a, b))
                        else "improved"
                        if b in {"found", "confirmed"}
                        else "regressed",
                    }
                )
    return result
