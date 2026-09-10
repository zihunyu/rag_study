"""Versioned acceptance contracts; expectations never enter retrieval or generation."""

from __future__ import annotations

import hashlib
import json
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
    return checks + list_check_results(case, result.get("answer") or "")
