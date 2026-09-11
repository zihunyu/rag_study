"""Propose additional source bindings without changing any answer fact."""

import re
from dataclasses import replace
from typing import Any

from ragkb.domain.citation_repair import (
    apply_citation_additions,
    citation_targets,
    validate_citation_only_change,
)
from ragkb.domain.rag import DraftAnswer, Evidence, VerificationResult


def repairable_grounding(draft: DraftAnswer, checked: VerificationResult) -> bool:
    facts = checked.verdicts[: len(draft.claims)]
    return bool(
        draft.synthesized
        and draft.claims
        and len(facts) == len(draft.claims)
        and checked.conflict_checked
        and checked.policy_checked
        and not checked.conflicting_evidence_ids
        and any(v.verdict == "INSUFFICIENT" for v in facts)
        and all(
            not c.visual_fact_ids
            and v.claim_text == c.text
            and v.evidence_ids == c.evidence_ids
            and v.verdict in {"SUPPORTED", "INSUFFICIENT"}
            for c, v in zip(draft.claims, facts, strict=True)
        )
    )


def apply_source_bindings(
    draft: DraftAnswer, bindings: Any, evidence: tuple[Evidence, ...]
) -> DraftAnswer:
    """Add existing authorized source IDs; return a candidate, never an approval."""
    if not isinstance(bindings, list) or len(bindings) > len(draft.claims):
        raise ValueError("GROUNDING_REPAIR_INVALID")
    sources = {e.evidence_id: e for e in evidence if e.authorized and e.current_version}
    claims = list(draft.claims)
    by_id = {f"C{i}": i - 1 for i in range(1, len(claims) + 1)}
    targets = citation_targets(draft.text)
    seen: set[str] = set()
    lines: dict[str, list[str]] = {}
    for item in bindings:
        if not isinstance(item, dict) or set(item) != {
            "claim_id",
            "evidence_additions",
            "line_ids",
        }:
            raise ValueError("GROUNDING_REPAIR_INVALID")
        identity, additions, line_ids = (
            item["claim_id"],
            item["evidence_additions"],
            item["line_ids"],
        )
        if (
            not isinstance(identity, str)
            or identity not in by_id
            or identity in seen
            or not isinstance(additions, list)
            or not additions
            or any(not isinstance(i, str) or i not in sources for i in additions)
            or len(set(additions)) != len(additions)
            or not isinstance(line_ids, list)
            or not line_ids
            or any(not isinstance(i, str) or i not in targets for i in line_ids)
            or len(set(line_ids)) != len(line_ids)
        ):
            raise ValueError("GROUNDING_REPAIR_INVALID")
        seen.add(identity)
        index = by_id[identity]
        old = claims[index]
        if old.visual_fact_ids or set(additions).intersection(old.evidence_ids):
            raise ValueError("GROUNDING_REPAIR_INVALID")
        claims[index] = replace(old, evidence_ids=(*old.evidence_ids, *additions))
        for line in line_ids:
            lines.setdefault(line, []).append(identity)
    revised = replace(
        draft,
        claims=tuple(claims),
        citation_ids=tuple(
            dict.fromkeys((*draft.citation_ids, *(i for c in claims for i in c.evidence_ids)))
        ),
    )
    candidate = apply_citation_additions(
        revised,
        [
            {"line_id": line, "claim_ids": ids}
            for line, ids in lines.items()
            if any(
                i not in set(re.findall(r"\[(E\d+)\]", targets[line]))
                for identity in ids
                for i in claims[by_id[identity]].evidence_ids
            )
        ],
    )
    validate_citation_only_change(revised, candidate)
    return candidate
