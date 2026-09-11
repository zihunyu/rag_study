"""Versioned acceptance contracts; expectations never enter retrieval or generation."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ragkb.application.reading_scope import ReadingOptions
from ragkb.domain.acceptance_points import PointSpec, list_check_results


class HistoryQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    reading: ReadingOptions = Field(default_factory=ReadingOptions)


class AcceptanceCase(PointSpec):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=80, pattern=r".*\S.*")
    question: str = Field(min_length=1, max_length=4000, pattern=r".*\S.*")
    category: str = Field(default="业务问答", max_length=100)
    reading: ReadingOptions = Field(default_factory=ReadingOptions)
    history: list[HistoryQuestion] = Field(default_factory=list, max_length=6)
    expected_status: Literal[
        "answered",
        "insufficient_evidence",
        "conflicting_evidence",
        "needs_clarification",
        "out_of_scope",
    ] = "answered"
    required_points: list[str] = Field(default_factory=list, max_length=30)
    forbidden_claims: list[str] = Field(default_factory=list, max_length=30)
    required_source_documents: list[str] = Field(default_factory=list, max_length=20)
    required_retrieved_documents: list[str] = Field(default_factory=list, max_length=20)
    minimum_distinct_cited_documents: int = Field(default=0, ge=0, le=20)
    source_notes: str = Field(default="", max_length=12000)
    review_state: Literal["candidate", "confirmed"] = "candidate"
    archived: bool = False


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def _missing_information_row(row: str, question: str) -> bool:
    """Recognize only an asked field followed solely by explicit absence markers.

    This is a citation-layout exception, not proof that the information is absent.
    The published answer must still pass semantic review and all case expectations.
    Unknown wording, policy denials and mixed factual cells remain citation-bearing.
    """
    cells = re.split(r"(?<!\\)\|", row.strip())[1:]
    if cells and not cells[-1].strip():
        cells.pop()
    cells = [c.strip().strip("*_` ").rstrip("。.!！").strip() for c in cells]
    if len(cells) < 2 or not cells[0] or cells[0].casefold() not in question.casefold():
        return False
    absence = (
        r"(?:(?:(?:现有|当前|所给|所提供|已提供|提供的|已提供的)(?:的)?)?"
        r"(?:资料|文档|信息)(?:中)?[，,:：]?)?未(?:提供|给出|说明|明确)|"
        r"not (?:provided|specified|stated)(?: in (?:the )?(?:supplied |provided )?"
        r"(?:documents|information|sources))?"
    )
    return all(re.fullmatch(absence, cell, re.I) is not None for cell in cells[1:])


def mechanical_checks(
    case: dict[str, Any],
    result: dict[str, Any],
    evidence: list[dict[str, Any]],
    allowed_documents: set[str],
) -> list[dict[str, Any]]:
    """Check observable invariants. Semantic correctness always needs separate review."""
    cited = {c["evidence_id"] for c in result.get("citations", [])}
    by_id = {e["evidence_id"]: e for e in evidence}
    docs = {by_id[c]["document_id"] for c in cited if c in by_id}
    expected = case["expected_status"]
    checks = [
        {
            "name": "回答状态符合预期",
            "passed": result.get("status") == expected,
            "detail": f"预期 {expected}，实际 {result.get('status')}",
        },
        {
            "name": "检索及引用未越过资料范围",
            "passed": all(e["document_id"] in allowed_documents for e in evidence)
            and cited <= by_id.keys(),
            "detail": "逐条核对证据所属文件与本题有效范围",
        },
        {
            "name": "必要来源齐全",
            "passed": set(case["required_source_documents"]) <= docs,
            "detail": "缺少：" + ", ".join(sorted(set(case["required_source_documents"]) - docs)),
        },
        {
            "name": "跨文件引用数量",
            "passed": len(docs) >= case["minimum_distinct_cited_documents"],
            "detail": f"实际引用 {len(docs)} 份文件",
        },
    ]
    checks.append(
        {
            "name": "必要检索证据齐全",
            "passed": set(case.get("required_retrieved_documents", []))
            <= {e["document_id"] for e in evidence},
            "detail": "冲突等拒答案例也要核对必要证据",
        }
    )
    checks.append(
        {
            "name": "答案发布状态",
            "passed": bool(result.get("answer") and result.get("verified"))
            if expected == "answered"
            else not result.get("answer") and not cited,
            "detail": "有答案须通过系统核验；预期拒答时不能发布正文或引用",
        }
    )
    if expected == "answered" and case.get("check_citation_structure"):
        from ragkb.domain.citation_repair import citation_targets

        answer = result.get("answer") or ""
        markers = set(re.findall(r"\[(E\d+)\]", answer))
        rows = [line for line in citation_targets(answer).values() if line.strip().startswith("|")]
        absence_rows = [
            row
            for row in rows
            if result.get("verified") is True
            and not re.search(r"\[E\d+\]", row)
            and _missing_information_row(row, case.get("question", ""))
        ]
        checks.extend(
            [
                {
                    "name": "正文引用对应已发布来源",
                    "passed": bool(markers) and markers <= cited,
                    "detail": "逐个核对正文引用编号；语义支持关系另由模型与人工复核。",
                },
                {
                    "name": "表格数据行引用齐全",
                    "passed": all(re.search(r"\[E\d+\]", r) or r in absence_rows for r in rows),
                    "detail": (
                        f"检查 {len(rows)} 行数据，其中 {len(absence_rows)} 行仅说明"
                        "所问字段资料不足，"
                        "不要求虚构引用；资料是否确实缺失及表前事实另行语义复核。"
                    ),
                },
            ]
        )
    return checks + list_check_results(case, result.get("answer") or "")
