"""Independent acceptance expectations, source bindings and observable list checks."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVALUATION_REVISION = "acceptance-v6-absence-row-citations-20260910"
RELEVANCE_POINT_ID = "__answer_relevance"


class SourceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=191)
    version_id: str = Field(min_length=1, max_length=191)
    chunk_id: str = Field(min_length=1, max_length=191)
    quote: str = Field(min_length=1, max_length=4000, pattern=r".*\S.*")


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    text: str = Field(min_length=1, max_length=2000, pattern=r".*\S.*")
    kind: Literal["required", "forbidden", "unknown"] = "required"
    sources: list[SourceBinding] = Field(default_factory=list, max_length=6)
    original_standard_id: str = Field(default="", max_length=191)
    original_check_id: str = Field(default="", max_length=80)


class ListChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_count: int | None = Field(default=None, ge=1, le=100)
    sequential: bool = False
    no_duplicates: bool = False


class PointReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    point_id: str = Field(min_length=1, max_length=80)
    status: Literal["covered", "missing", "incorrect", "pending_review"]
    answer_quote: str = Field(default="", max_length=4000)
    note: str = Field(min_length=1, max_length=2000, pattern=r".*\S.*")


class PointSpec(BaseModel):
    criteria: list[Criterion] = Field(default_factory=list, max_length=30)
    list_checks: ListChecks = Field(default_factory=ListChecks)
    check_relevance: bool = False
    check_citation_structure: bool = False

    @model_validator(mode="after")
    def distinct_ids(self) -> PointSpec:
        if len({p.id for p in self.criteria}) != len(self.criteria):
            raise ValueError("DUPLICATE_POINT_ID")
        if any(p.id == RELEVANCE_POINT_ID for p in self.criteria):
            raise ValueError("RESERVED_POINT_ID")
        return self


def criteria_for(case: dict[str, Any]) -> list[dict[str, Any]]:
    points = (
        list(case["criteria"])
        if case.get("criteria")
        else [
            {"id": f"{kind}-{i}", "kind": kind, "text": text, "sources": []}
            for kind, key in (("required", "required_points"), ("forbidden", "forbidden_claims"))
            for i, text in enumerate(case.get(key, []), 1)
        ]
    )
    if case.get("check_relevance"):
        sources = []
        for point in points:
            for source in point.get("sources", []):
                if source not in sources:
                    sources.append(source)
        points.append(
            {
                "id": RELEVANCE_POINT_ID,
                "kind": "relevance",
                "text": "回答围绕问题；保留必要条件、例外、单位和范围，"
                "不混入无关条款，不重复附加相同内容。共享条件必须明确覆盖全部适用对象；"
                "例外不得扩大到其他规则。段落、数据行与单位说明中的事实均应就近标注来源，"
                "不得把漏答或缩小条件范围视为精简。",
                "sources": sources,
            }
        )
    return points


def pending_points(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "point_id": p["id"],
            "status": "pending_review",
            "answer_quote": "",
            "note": "尚未逐项评审",
            "origin": "pending",
        }
        for p in criteria_for(case)
    ]


def list_entries(answer: str) -> list[tuple[str, str]]:
    # Count only top-level entries; fenced code and nested sublists do not count.
    entries: list[tuple[str, str]] = []
    fenced = False
    for line in answer.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = re.match(r"^(?:[（(](\d{1,3})[)）]|(\d{1,3})[.、．](?!\d)|([-*+]))\s*(\S.*)$", line)
        if m:
            entries.append((m[1] or m[2] or m[3], m[4]))
        elif entries and line.strip() and line[:1].isspace():
            number, text = entries[-1]
            entries[-1] = (number, text + " " + line.strip())
    return entries


def list_check_results(case: dict[str, Any], answer: str) -> list[dict[str, Any]]:
    options = case.get("list_checks") or {}
    if not any(options.values()):
        return []
    entries = list_entries(answer)
    checks = []
    if options.get("expected_count") is not None:
        checks.append(
            {
                "name": "清单条目数量",
                "passed": len(entries) == options["expected_count"],
                "detail": f"预期 {options['expected_count']} 项，实际 {len(entries)} 项；"
                "数量不代表语义覆盖。",
            }
        )
    if options.get("sequential"):
        checks.append(
            {
                "name": "清单连续编号",
                "passed": bool(entries)
                and [n for n, _ in entries] == [str(i) for i in range(1, len(entries) + 1)],
                "detail": "按原始回答检查从 1 开始连续编号，不依赖页面自动重编号。",
            }
        )
    if options.get("no_duplicates"):
        keys = [
            re.sub(r"\s+", "", unicodedata.normalize("NFKC", re.sub(r"\[E\d+\]", "", body))).rstrip(
                "。.;；"
            )
            for _, body in entries
        ]
        duplicates = [i for i, key in enumerate(keys, 1) if key in keys[: i - 1]]
        checks.append(
            {
                "name": "清单无重复条目",
                "passed": not duplicates,
                "detail": "重复项：" + "、".join(map(str, duplicates))
                if duplicates
                else "未发现文字相同的重复项；语义重复另行复核。",
            }
        )
    return checks


def point_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(rows),
        **{
            status: sum(r["status"] == status for r in rows)
            for status in ("covered", "missing", "incorrect", "pending_review")
        },
    }


def effective_points(attempt: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not attempt:
        return []
    for review in reversed(attempt.get("reviews", [])):
        if review.get("point_reviews"):
            return [{**p, "origin": "human"} for p in review["point_reviews"]]
    return list(attempt.get("payload", {}).get("point_results", []))


def compare_points(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    before, after = effective_points(left), effective_points(right)
    old = {p["point_id"]: p for p in before}
    rows = []
    for point in after:
        previous = old.get(point["point_id"], {})
        a, b = previous.get("status", "pending_review"), point["status"]
        change = (
            "pending"
            if "pending_review" in (a, b)
            else "unchanged"
            if a == b
            else "regressed"
            if a == "covered"
            else "improved"
            if b == "covered"
            else "changed"
        )
        rows.append({"point_id": point["point_id"], "before": a, "after": b, "change": change})
    return {
        "before": point_summary(before),
        "after": point_summary(after),
        "rows": rows,
        "regressed": any(r["change"] == "regressed" for r in rows),
    }
