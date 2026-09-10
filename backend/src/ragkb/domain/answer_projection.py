"""Bounded, independently checked removal of a table duplicated by its lead paragraph."""

from __future__ import annotations

import re
from typing import Any

from ragkb.domain.rag import DraftAnswer, VerificationResult

PROJECTION_REVIEW_RULES = (
    "If duplicate_table_candidate is supplied, independently compare "
    "that exact existing paragraph with the COMPLETE original answer. "
    "Return an OPTIONAL projection_check: {all_content_preserved: "
    "boolean, citations_valid: boolean, retained_claim_ids: [C#...]}. "
    "Approve ONLY when the candidate already expresses EVERY original "
    "material fact AND every input claim, retaining all entities, "
    "values, units, relationships, conditions, exceptions, uncertainty "
    "and scope with valid inline citations. Matching words or numbers "
    "is not proof. If the table adds even one fact, branch or binding, "
    "all_content_preserved must be false. For approval list EVERY input "
    "claim ID in order. Do not rewrite the candidate. Missing/uncertain "
    "approval keeps the original answer. This optional comparison "
    "does not replace or change the ORIGINAL answer_check, claims, "
    "conditions, or full-pool conflict checks; complete them normally. "
)


def duplicate_table_candidate(question: str, draft: DraftAnswer) -> str:
    if not draft.synthesized or re.search(r"表格|列表|对照表|用表|制表|\btable\b", question, re.I):
        return ""
    parts = re.split(r"\n\s*\n", draft.text.strip())
    if len(parts) != 2 or not draft.claims:
        return ""
    paragraph, table = parts
    rows = table.splitlines()
    if (
        paragraph.lstrip().startswith(("|", "#", ">", "```", "~~~", "- ", "* "))
        or len(rows) < 3
        or not all(row.strip().startswith("|") and row.strip().endswith("|") for row in rows)
        or not re.fullmatch(r"[|\s:\-]+", rows[1])
    ):
        return ""

    # Only a candidate, never proof of semantic equivalence. Avoid requesting an
    # optional model review when numbers or source markers would obviously vanish.
    def markers(value: str) -> set[str]:
        return set(re.findall(r"\[E\d+\]", value))

    def numbers(value: str) -> set[str]:
        return set(re.findall(r"\d+(?:\.\d+)?", re.sub(r"\[E\d+\]", "", value)))

    if not markers(paragraph) or markers(paragraph) != markers(draft.text):
        return ""
    if numbers(paragraph) != numbers(draft.text):
        return ""
    return paragraph


def approved_projection(question: str, draft: DraftAnswer, receipt: Any) -> str:
    candidate = duplicate_table_candidate(question, draft)
    if not candidate or not isinstance(receipt, dict):
        return ""
    expected = [f"C{i}" for i in range(1, len(draft.claims) + 1)]
    if (
        receipt.get("all_content_preserved") is not True
        or receipt.get("citations_valid") is not True
        or receipt.get("retained_claim_ids") != expected
    ):
        return ""
    return candidate


def verified_projection(question: str, draft: DraftAnswer, result: VerificationResult) -> str:
    """No unchecked rewriting: keep the original paragraph and every condition witness."""
    candidate = duplicate_table_candidate(question, draft)
    if not result.supported or not candidate or result.answer_projection != candidate:
        return ""
    if any(
        check["status"] == "covered"
        and (not check.get("answer_quote") or check["answer_quote"] not in candidate)
        for check in result.condition_checks
    ):
        return ""
    return candidate
