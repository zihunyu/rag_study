"""Make the single claim-ledger source explicit before full answer verification."""

import re

from ragkb.domain.rag import AtomicClaim


def attach_single_source_citations(answer: str, claims: tuple[AtomicClaim, ...]) -> str:
    sources = {identity for claim in claims for identity in claim.evidence_ids}
    if len(sources) != 1 or not claims:
        return answer
    identity = next(iter(sources))
    lines = answer.splitlines()
    table = False
    changed = False
    fenced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        if not stripped.startswith("|"):
            table = False
            if stripped and not stripped.startswith("#") and not re.search(r"\[E\d+\]", line):
                lines[index] = line.rstrip() + f" [{identity}]"
                changed = True
            continue
        if re.fullmatch(r"[|\s:\-]+", stripped) and "---" in stripped:
            table = True
            continue
        if table and stripped.endswith("|") and not re.search(r"\[E\d+\]", line):
            # This only makes the supplied ledger's attribution explicit. The
            # complete resulting answer MUST still pass the normal fact, claim,
            # condition and full-pool conflict verifier before release.
            at = line.rfind("|")
            lines[index] = line[:at].rstrip() + f" [{identity}] " + line[at:]
            changed = True
    return ("\n".join(lines) + ("\n" if answer.endswith("\n") else "")) if changed else answer
