"""Bind diagram claims to specific version-scoped facts rather than their whole image."""

from __future__ import annotations

from dataclasses import replace

from ragkb.domain.rag import AtomicClaim, Evidence


def visual_claim_evidence(
    claim: AtomicClaim, evidence: tuple[Evidence, ...]
) -> tuple[Evidence, ...]:
    """Validate membership and narrow graph evidence to declared facts for semantic review."""
    cited = [e for e in evidence if e.evidence_id in claim.evidence_ids]
    allowed = {
        f["fact_id"]: (e, f)
        for e in cited
        for f in e.locator.get("visual_facts", [])
        if isinstance(f, dict) and isinstance(f.get("fact_id"), str)
    }
    selected = set(claim.visual_fact_ids)
    if len(selected) != len(claim.visual_fact_ids) or selected - allowed.keys():
        raise ValueError("CLAIM_VISUAL_FACT_INVALID")
    for identity in selected:
        source, fact = allowed[identity]
        if (
            not source.authorized
            or not source.current_version
            or fact.get("asset_id") not in source.locator.get("visual_asset_ids", [])
            or fact.get("document_version_id", source.document_version_id)
            != source.document_version_id
            or fact.get("review_status") in {"pending", "excluded"}
            or not isinstance(fact.get("text"), str)
            or fact["text"] not in source.text
        ):
            raise ValueError("CLAIM_VISUAL_FACT_SCOPE_INVALID")
    result = []
    for source in cited:
        source_fact_ids = {
            f["fact_id"]
            for f in source.locator.get("visual_facts", [])
            if isinstance(f, dict) and isinstance(f.get("fact_id"), str)
        }
        if source_fact_ids and not source_fact_ids.intersection(selected):
            raise ValueError("CLAIM_VISUAL_FACT_REQUIRED")
        if source_fact_ids:
            facts = [f for f in source.locator["visual_facts"] if f["fact_id"] in selected]
            coverage = str(source.locator.get("visual_coverage_quote", ""))
            result.append(
                replace(
                    source,
                    text="\n".join(
                        [*(f["text"] for f in facts), *([coverage] if coverage else [])]
                    ),
                    locator={**source.locator, "visual_facts": facts},
                )
            )
        else:
            result.append(source)
    return tuple(result)


def used_visual_fact_ids(claims: tuple[AtomicClaim, ...], source: Evidence) -> list[str]:
    allowed = {f["fact_id"] for f in source.locator.get("visual_facts", [])}
    return list(
        dict.fromkeys(
            identity
            for claim in claims
            if source.evidence_id in claim.evidence_ids
            for identity in claim.visual_fact_ids
            if identity in allowed
        )
    )
