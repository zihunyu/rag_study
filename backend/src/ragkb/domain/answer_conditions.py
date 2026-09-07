"""Source-anchored obligations: checking answer truth is not checking omitted conditions."""

from __future__ import annotations

import re
from typing import Any

from ragkb.domain.rag import DraftAnswer, Evidence

_CONDITION = re.compile(
    r"仅(?:限|当|在|适用|支持|提供|允许)?|必须|不得|禁止|不能|不支持|不适用|不包括|不包含|不涵盖|不含|"
    r"需(?:要|先|提供|满足|经过)|应当|须|"
    r"除外|除非|否则|如果|假如|若|当.{0,60}时|前提|限定条件|适用范围|注意事项|"
    r"至少|至多|不超过|不得超过|上限|下限|有效期|条件[：:]|范围[：:]|"
    r"\b(?:only|unless|except|otherwise|if|when|must|required|shall|cannot|"
    r"not|never|provided\s+that|subject\s+to|at\s+least|at\s+most|maximum|minimum)\b|"
    r"[<>≤≥]=?\s*\d",
    re.I,
)


def _has_condition_witness(source: str, quote: str) -> bool:
    """Reject vacuous 'covered' witnesses; semantic review still checks their meaning."""

    def anchors(text: str) -> set[str]:
        text = re.sub(r"\[E[^\]]+\]", "", text).casefold()
        text = re.sub(
            r"设备|产品|系统|资料|内容|相关|可以|支持|提供|仅限|位于|包括|不包括|适用|条件",
            "",
            text,
        )
        terms = {
            word
            for word in re.findall(r"[a-z]{2,}|\d+(?:\.\d+)?", text)
            if word
            not in {
                "the",
                "is",
                "are",
                "and",
                "or",
                "in",
                "of",
                "to",
                "for",
                "only",
                "must",
                "device",
                "product",
                "system",
            }
        }
        for part in re.findall(r"[\u4e00-\u9fff]+", text):
            terms.update(part[i : i + 2] for i in range(len(part) - 1))
        return terms

    if anchors(source).intersection(anchors(quote)):
        return True
    different_language = bool(re.search(r"[\u4e00-\u9fff]", source)) != bool(
        re.search(r"[\u4e00-\u9fff]", quote)
    )
    return different_language and bool(_CONDITION.search(quote))


def condition_quotes(text: str) -> list[str]:
    # Preserve each complete source sentence / table row, including its qualifier.
    # Dots in decimals and English abbreviations are deliberately not split.
    result = []
    table_conditions = False
    parts = re.split(r"(?<=[。！？!?])|\n", text)
    for index, part in enumerate(parts):
        value = part.strip()
        if value.startswith("|"):
            if not re.search(r"[^\s|:\-]", value):
                continue
            if (
                index + 1 < len(parts)
                and re.fullmatch(r"[\s|:\-]+", parts[index + 1])
                and "---" in parts[index + 1]
            ):
                table_conditions = bool(
                    re.search(
                        r"条件|适用|地区|区域|范围|例外|限制|前提|\b(condition|region|scope|exception|eligibility)\b",
                        value,
                        re.I,
                    )
                )
                continue
            if table_conditions and re.search(r"[^\s|:\-]", value):
                result.append(value)
        else:
            table_conditions = False
        if value and not value.startswith("#") and _CONDITION.search(value):
            result.append(value)
    return list(dict.fromkeys(result))


def condition_requirements(evidence: tuple[Evidence, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in evidence:
        for quote in condition_quotes(item.text):
            key = (
                item.document_version_id,
                str(item.locator.get("section_path", item.chunk_id)),
                quote,
            )
            if key in seen:
                seen[key]["equivalent_evidence_ids"].append(item.evidence_id)
                continue
            requirement = {
                "id": f"K{len(result) + 1}",
                "evidence_id": item.evidence_id,
                "source_quote": quote,
                "equivalent_evidence_ids": [item.evidence_id],
            }
            seen[key] = requirement
            result.append(requirement)
    return result


def validate_condition_checks(
    raw: Any,
    required: list[dict[str, Any]],
    draft: DraftAnswer,
) -> tuple[dict[str, str], ...]:
    if not required:
        return ()
    if not isinstance(raw, list) or len(raw) != len(required):
        raise ValueError("VERIFIER_CONDITION_CHECK_REQUIRED")
    checks = []
    for expected, check in zip(required, raw, strict=True):
        if not isinstance(check, dict) or check.get("id") != expected["id"]:
            raise ValueError("VERIFIER_CONDITION_ID_INVALID")
        status = check.get("status")
        quote, reason = check.get("answer_quote"), check.get("reason")
        if (
            status not in {"covered", "missing", "not_applicable"}
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError("VERIFIER_CONDITION_VERDICT_INVALID")
        if (
            not isinstance(quote, str)
            or (
                status == "covered"
                and (
                    not quote.strip()
                    or quote not in draft.text
                    or not _has_condition_witness(expected["source_quote"], quote)
                    or not set(expected["equivalent_evidence_ids"]).intersection(draft.citation_ids)
                )
            )
            or (status != "covered" and quote)
        ):
            raise ValueError("VERIFIER_CONDITION_WITNESS_INVALID")
        checks.append(
            {
                "id": expected["id"],
                "evidence_id": expected["evidence_id"],
                "source_quote": expected["source_quote"],
                "status": status,
                "answer_quote": quote,
                "reason": reason[:500],
            }
        )
    return tuple(checks)


def condition_report(checks: tuple[dict[str, str], ...]) -> dict[str, Any]:
    # Public history contains counts, not stale source excerpts after revocation.
    return {
        "checked": len(checks),
        "covered": sum(c["status"] == "covered" for c in checks),
        "not_applicable": sum(c["status"] == "not_applicable" for c in checks),
        "missing": sum(c["status"] == "missing" for c in checks),
        "complete": all(c["status"] != "missing" for c in checks),
    }
