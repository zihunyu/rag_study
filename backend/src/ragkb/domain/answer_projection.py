"""Bounded, independently checked removal of redundant answer content."""

from __future__ import annotations

import re
from typing import Any

from ragkb.domain.rag import DraftAnswer, VerificationResult

PROJECTION_REVIEW_RULES = (
    "If duplicate_table_candidate is supplied, independently compare "
    "that exact retained text with the COMPLETE original answer. The candidate "
    "removes a duplicate table or one post-table recap sentence, without adding "
    "or rewriting text. A recap cue is NOT proof that its content is redundant. "
    "Return an OPTIONAL projection_check: {all_content_preserved: "
    "boolean, citations_valid: boolean, retained_claim_ids: [C#...]}. "
    "Approve ONLY when the candidate already expresses EVERY original "
    "material fact AND every input claim, retaining all entities, "
    "values, units, relationships, conditions, exceptions, uncertainty "
    "and scope with valid inline citations. Matching words or numbers "
    "is not proof. If the removed text adds even one fact, branch or binding, "
    "all_content_preserved must be false. For approval list EVERY input "
    "claim ID in order. Do not rewrite the candidate. Missing/uncertain "
    "approval keeps the original answer. This optional comparison "
    "does not replace or change the ORIGINAL answer_check, claims, "
    "conditions, or full-pool conflict checks; complete them normally. "
)


def _is_table(table: str) -> bool:
    rows = table.splitlines()
    return bool(
        len(rows) >= 3
        and all(row.strip().startswith("|") and row.strip().endswith("|") for row in rows)
        and re.fullmatch(r"[|\s:\-]+", rows[1])
    )


def _retains_markers_and_numbers(candidate: str, original: str) -> bool:
    # Only a candidate, never proof of semantic equivalence. Avoid requesting an
    # optional model review when numbers or source markers would obviously vanish.
    def markers(value: str) -> set[str]:
        return set(re.findall(r"\[E\d+\]", value))

    def numbers(value: str) -> set[str]:
        return set(re.findall(r"\d+(?:\.\d+)?", re.sub(r"\[E\d+\]", "", value)))

    return bool(
        markers(candidate)
        and markers(candidate) == markers(original)
        and numbers(candidate) == numbers(original)
    )


def duplicate_table_candidate(question: str, draft: DraftAnswer) -> str:
    if (
        not draft.synthesized
        or not draft.claims
        or len(draft.text) > 8000
        or re.search(
            r"表格|列表|对照表|用表|制表|复述|重述|\b(?:table|restate|repeat)\b", question, re.I
        )
    ):
        return ""
    parts = re.split(r"\n\s*\n", draft.text.strip())
    if len(parts) == 2:
        paragraph, table = parts
        if (
            not paragraph.lstrip().startswith(("|", "#", ">", "```", "~~~", "- ", "* "))
            and _is_table(table)
            and _retains_markers_and_numbers(paragraph, draft.text)
        ):
            return paragraph
    # At most one optional candidate, reviewed in the existing full verification
    # call. Keep the table, intro, and every subsequent sentence verbatim.
    if 2 <= len(parts) <= 3 and _is_table(parts[-2]):
        recap = parts[-1]
        if re.match(r"(?:也就是说|换言之|换句话说|In other words\b|That is\b)", recap, re.I):
            sentence = re.match(r".*?(?:[。！？!?]|\.(?=\s|$))(?:\s*\[E\d+\])*", recap)
            if sentence:
                rest = recap[sentence.end():].strip()
                candidate = "\n\n".join(parts[:-1] + ([rest] if rest else []))
                if _retains_markers_and_numbers(candidate, draft.text):
                    return candidate
    return ""


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
    """No unchecked rewriting: keep retained text and every condition witness."""
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
