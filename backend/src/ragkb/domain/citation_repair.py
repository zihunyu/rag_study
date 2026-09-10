"""Citation-only edits: never rewrite facts, invent sources or approve an answer."""

from __future__ import annotations

import re
from dataclasses import replace
from itertools import groupby
from typing import Any

from ragkb.domain.rag import AtomicClaim, DraftAnswer, Evidence, VerificationResult


def repairable_citations(draft: DraftAnswer, result: VerificationResult) -> bool:
    facts = result.verdicts[: len(draft.claims)]
    return bool(
        draft.synthesized
        and draft.claims
        and not result.citation_ids_valid
        and result.answer_claims_covered
        and result.conflict_checked
        and result.policy_checked
        and not result.conflicting_evidence_ids
        and len(facts) == len(draft.claims)
        and all(
            verdict.verdict == "SUPPORTED"
            and verdict.claim_text == claim.text
            and verdict.evidence_ids == claim.evidence_ids
            for claim, verdict in zip(draft.claims, facts, strict=True)
        )
        and all(
            verdict.verdict == "SUPPORTED"
            or (verdict.claim_text == draft.text and not verdict.evidence_ids)
            or verdict.reason_code == "ANSWER_KEY_CONDITION_MISSING"
            for verdict in result.verdicts[len(draft.claims) :]
        )
    )


def repairable_surface(draft: DraftAnswer, result: VerificationResult) -> bool:
    """Only prose disagrees with an entirely supported, source-bound claims ledger."""
    facts = result.verdicts[: len(draft.claims)]
    surface = result.verdicts[len(draft.claims) :]
    return bool(
        draft.synthesized
        and draft.claims
        and not result.answer_claims_covered
        and result.conflict_checked
        and result.policy_checked
        and not result.conflicting_evidence_ids
        and len(facts) == len(draft.claims)
        and all(
            claim.evidence_ids
            and set(claim.evidence_ids).issubset(draft.citation_ids)
            and verdict.verdict == "SUPPORTED"
            and verdict.claim_text == claim.text
            and verdict.evidence_ids == claim.evidence_ids
            for claim, verdict in zip(draft.claims, facts, strict=True)
        )
        and len(surface) == 1
        and surface[0].claim_text == draft.text
        and not surface[0].evidence_ids
        and surface[0].verdict == "INSUFFICIENT"
    )


def rebuild_from_supported_claims(
    draft: DraftAnswer, *, numbered: bool = False, list_claim_count: int | None = None
) -> DraftAnswer:
    """Preserve every claim verbatim with its own citations; still requires full review."""
    if numbered:
        count = len(draft.claims) if list_claim_count is None else list_claim_count
        if not 0 < count <= len(draft.claims):
            raise ValueError("INVALID_LIST_CLAIM_COUNT")
        main = "\n\n".join(
            f"{index}. {claim.text} " + "".join(f"[{identity}]" for identity in claim.evidence_ids)
            for index, claim in enumerate(draft.claims[:count], 1)
        )
        supplement = "\n\n".join(
            claim.text + " " + "".join(f"[{identity}]" for identity in claim.evidence_ids)
            for claim in draft.claims[count:]
        )
        return replace(
            draft,
            text=main + ("\n\n相关限制：\n\n" + supplement if supplement else ""),
        )
    return replace(
        draft,
        text="\n\n".join(
            " ".join(
                claim.text + " " + "".join(f"[{identity}]" for identity in claim.evidence_ids)
                for claim in group
            )
            for _, group in groupby(draft.claims, key=lambda claim: claim.evidence_ids)
        ),
    )


def append_missing_text_conditions(
    draft: DraftAnswer, result: VerificationResult, evidence: tuple[Evidence, ...]
) -> DraftAnswer | None:
    """Complete plain-text omissions from exact source quotes, without rewriting facts."""
    facts = result.verdicts[: len(draft.claims)]
    missing = [c for c in result.condition_checks if c["status"] == "missing"]
    if not (
        draft.synthesized
        and draft.claims
        and missing
        and result.answer_claims_covered
        and result.citation_ids_valid
        and result.conflict_checked
        and result.policy_checked
        and not result.conflicting_evidence_ids
        and len(facts) == len(draft.claims)
        and all(
            not claim.visual_fact_ids
            and verdict.verdict == "SUPPORTED"
            and verdict.claim_text == claim.text
            and verdict.evidence_ids == claim.evidence_ids
            for claim, verdict in zip(draft.claims, facts, strict=True)
        )
        and all(
            v.reason_code == "ANSWER_KEY_CONDITION_MISSING"
            for v in result.verdicts[len(draft.claims) :]
        )
    ):
        return None
    by_id = {e.evidence_id: e for e in evidence}
    additions: list[AtomicClaim] = []
    for check in missing:
        source = by_id.get(check["evidence_id"])
        quote = check["source_quote"]
        if (
            source is None
            or not source.authorized
            or not source.current_version
            or source.locator.get("visual_facts")
            or not quote.strip()
            or quote not in source.text
        ):
            return None
        additions.append(AtomicClaim(quote, (source.evidence_id,)))
    return replace(
        draft,
        text=draft.text
        + "\n\n相关限制：\n\n"
        + "\n\n".join(c.text + f" [{c.evidence_ids[0]}]" for c in additions),
        claims=(*draft.claims, *additions),
        citation_ids=tuple(
            dict.fromkeys((*draft.citation_ids, *(c.evidence_ids[0] for c in additions)))
        ),
    )


def citation_targets(text: str) -> dict[str, str]:
    targets = {}
    fenced = False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        value = line.strip()
        if value.startswith(("```", "~~~")):
            fenced = not fenced
        elif value and not fenced:
            if re.fullmatch(r"[|\s:\-]+", value):
                continue
            if (
                value.startswith("|")
                and index + 1 < len(lines)
                and re.fullmatch(r"[|\s:\-]+", lines[index + 1])
            ):
                continue
            targets[f"L{index + 1}"] = line
    return targets


def apply_citation_additions(draft: DraftAnswer, additions: Any) -> DraftAnswer:
    targets = citation_targets(draft.text)
    if not isinstance(additions, list) or len(additions) > len(targets):
        raise ValueError("CITATION_REPAIR_INVALID")
    claims = {f"C{i}": claim for i, claim in enumerate(draft.claims, 1)}
    lines = draft.text.splitlines(keepends=True)
    seen: set[str] = set()
    for item in additions:
        if not isinstance(item, dict) or set(item) != {"line_id", "claim_ids"}:
            raise ValueError("CITATION_REPAIR_INVALID")
        identity, ids = item["line_id"], item["claim_ids"]
        if (
            not isinstance(identity, str)
            or identity not in targets
            or identity in seen
            or not isinstance(ids, list)
            or not ids
            or any(not isinstance(i, str) or i not in claims for i in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("CITATION_REPAIR_INVALID")
        seen.add(identity)
        sources = tuple(dict.fromkeys(e for i in ids for e in claims[i].evidence_ids))
        if not set(sources).issubset(draft.citation_ids):
            raise ValueError("CITATION_REPAIR_INVALID")
        index = int(identity[1:]) - 1
        line = lines[index]
        existing = set(re.findall(r"\[(E\d+)\]", line))
        sources = tuple(source for source in sources if source not in existing)
        if not sources:
            raise ValueError("CITATION_REPAIR_INVALID")
        at = len(line.rstrip("\r\n"))
        if line.lstrip().startswith("|") and line[:at].rstrip().endswith("|"):
            at = line.rfind("|", 0, at)
        markers = " " + "".join(f"[{source}]" for source in sources)
        lines[index] = line[:at] + markers + line[at:]
    return replace(draft, text="".join(lines))


def validate_citation_only_change(before: DraftAnswer, after: DraftAnswer) -> None:
    if replace(after, text=before.text) != before:
        raise ValueError("CITATION_REPAIR_CHANGED_FACTS")
    old_lines, new_lines = before.text.splitlines(), after.text.splitlines()
    if len(old_lines) != len(new_lines):
        raise ValueError("CITATION_REPAIR_CHANGED_FACTS")
    for old, new in zip(old_lines, new_lines, strict=True):
        if re.sub(r"\s*\[E\d+\]", "", old) != re.sub(r"\s*\[E\d+\]", "", new):
            raise ValueError("CITATION_REPAIR_CHANGED_FACTS")
        old_ids, new_ids = set(re.findall(r"\[(E\d+)\]", old)), set(re.findall(r"\[(E\d+)\]", new))
        if not old_ids.issubset(new_ids) or not new_ids.issubset(before.citation_ids):
            raise ValueError("CITATION_REPAIR_CHANGED_SOURCES")
