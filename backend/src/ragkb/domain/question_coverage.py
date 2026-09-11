"""Required question aspects and evidence-to-answer verification receipts."""

from __future__ import annotations

import re
from typing import Any

from ragkb.domain.errors import InvalidProviderResponse
from ragkb.domain.rag import ClaimVerdict, DraftAnswer, Evidence


def question_aspects(question: str) -> tuple[str, ...]:
    original = question.strip()
    pieces: list[str] = []
    current: list[str] = []
    closers: list[str] = []
    pairs = {"(": ")", "（": "）", "[": "]", "【": "】", "“": "”", "「": "」", '"': '"', "`": "`"}
    for char in original:
        if closers and char == closers[-1]:
            closers.pop()
        elif char in pairs:
            closers.append(pairs[char])
        if not closers and char in "；;？?。\n、":
            pieces.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    pieces.append("".join(current).strip())
    if len([p for p in pieces if len(p) >= 2]) < 2 and not any(c in original for c in pairs):
        pieces = re.split(r"以及|同时|另外|并且|，(?=如何|是否|哪些|什么|怎么|能否|多少)", original)
    facets = list(dict.fromkeys(p.strip(" ，,。？?；;") for p in pieces if len(p.strip()) >= 2))
    if len(facets) < 2 and not any(c in original for c in pairs):
        attributes = re.fullmatch(
            r"(?:请)?(?:给出|列出|说明|介绍|提供|查询|告诉我)(.{1,80}?)的(.{2,160})[？?。]?",
            original,
        )
        if attributes:
            parts = re.split(r"和|以及|及|与", attributes[2].strip("？?。"))
            if len(parts) > 1 and all(len(p.strip()) >= 2 for p in parts):
                facets = [f"{attributes[1]}的{part.strip()}" for part in parts]
    if len(facets) < 2:
        comparison = re.fullmatch(
            r"(?:请)?(?:比较|对比)(.{1,60}?)(?:和|与)(.{1,60}?)的(.{2,100})[？?。]?", original
        )
        facets = (
            [f"{name}的{comparison[3]}" for name in (comparison[1], comparison[2])]
            if comparison
            else [original]
        )
    # Keep overflow explicit as a compound requirement, never silently drop it.
    return tuple(facets[:31] + ["；".join(facets[31:])] if len(facets) > 32 else facets)


def required_aspects(question: str) -> list[dict[str, str]]:
    return [
        {"aspect_id": f"A{i}", "question": value}
        for i, value in enumerate(question_aspects(question), 1)
    ]


ASPECT_REVIEW_RULES = (
    " Also return aspect_checks, exactly one row for EVERY required_aspects aspect_id. "
    "Each row has aspect_id, status (answered, partial, evidence_missing, answer_missing, "
    "ambiguous), evidence_ids, claim_ids and answer_quote. Check the aspect under ALL "
    "original question conditions. answered requires the COMPLETE requested aspect "
    "to be answered by the actual displayed answer, "
    "SUPPORTED claims and their own evidence. Matching words or a citation alone is not coverage. "
    "answer_quote must be an exact substring of answer; claim_ids refer to input C# claims. "
    "For answer_missing, evidence exists but the answer omits it; evidence_missing means only that "
    "this supplied pool is insufficient, never the entire corpus. "
    "For unanswered rows use empty "
    "claim_ids and answer_quote. Partial requires supported answered content "
    "and an unresolved part. "
)


def validate_aspect_checks(
    question: str,
    value: Any,
    draft: DraftAnswer,
    evidence: tuple[Evidence, ...],
    verdicts: tuple[ClaimVerdict, ...],
) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()  # Legacy receipts are explicitly unchecked at presentation.
    expected = {row["aspect_id"] for row in required_aspects(question)}
    sources = {e.evidence_id for e in evidence}
    claims = {f"C{i}": c for i, c in enumerate(draft.claims, 1)}
    supported = {
        f"C{i}" for i, v in enumerate(verdicts[: len(draft.claims)], 1) if v.verdict == "SUPPORTED"
    }
    try:
        if not isinstance(value, list) or len(value) != len(expected):
            raise ValueError
        seen = set()
        for row in value:
            identity, status = row["aspect_id"], row["status"]
            ids, refs, quote = row["evidence_ids"], row["claim_ids"], row["answer_quote"]
            if (
                identity not in expected
                or identity in seen
                or status
                not in {"answered", "partial", "evidence_missing", "answer_missing", "ambiguous"}
            ):
                raise ValueError
            seen.add(identity)
            if any(
                not isinstance(items, list)
                or any(not isinstance(i, str) for i in items)
                or len(set(items)) != len(items)
                for items in (ids, refs)
            ):
                raise ValueError
            if (
                not set(ids) <= sources
                or not set(refs) <= set(claims)
                or not isinstance(quote, str)
            ):
                raise ValueError
            if status in {"answered", "partial"}:
                if (
                    not refs
                    or not ids
                    or not quote.strip()
                    or quote not in draft.text
                    or not set(refs) <= supported
                ):
                    raise ValueError
                cited = {e for ref in refs for e in claims[ref].evidence_ids}
                if set(ids) != cited:
                    raise ValueError
            elif refs or quote or (status == "answer_missing" and not ids):
                raise ValueError
        return tuple(
            {
                k: row[k]
                for k in ("aspect_id", "status", "evidence_ids", "claim_ids", "answer_quote")
            }
            for row in value
        )
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidProviderResponse("VERIFIER_ASPECT_COVERAGE_INVALID") from error


def coverage_report(
    question: str,
    checks: tuple[dict[str, Any], ...],
    selected: list[dict[str, Any]],
    answer: str | None,
    cited_ids: set[str],
) -> dict[str, Any]:
    by_id = {row["aspect_id"]: row for row in checks}
    sources = {row["aspect_id"]: row for row in selected}
    rows = []
    for required in required_aspects(question):
        source = sources.get(required["aspect_id"], {})
        check = by_id.get(required["aspect_id"], {})
        status = check.get("status", "unchecked")
        quote = check.get("answer_quote", "")
        ids = check.get("evidence_ids", source.get("evidence_ids", []))
        if status in {"answered", "partial"} and (
            not answer or not quote or quote not in answer or not set(ids) <= cited_ids
        ):
            status, quote = "unchecked", ""
        rows.append(
            {
                **required,
                "evidence_status": {
                    "answered": "supported",
                    "partial": "partial",
                    "answer_missing": "supported",
                    "evidence_missing": "missing",
                    "ambiguous": "ambiguous",
                }.get(check.get("status", ""), source.get("status", "unchecked")),
                "evidence_ids": ids,
                "answer_status": status,
                "claim_ids": check.get("claim_ids", []) if quote else [],
                "answer_quote": quote,
            }
        )
    return {
        "revision": "required-aspects-v1",
        "complete": all(row["answer_status"] == "answered" for row in rows),
        "items": rows,
        "answered": sum(row["answer_status"] == "answered" for row in rows),
        "total": len(rows),
    }
