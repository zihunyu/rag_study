"""Make the single claim-ledger source explicit before full answer verification."""

import re

from ragkb.domain.rag import AtomicClaim


def attach_bound_citations(
    answer: str, claims: tuple[AtomicClaim, ...], quotes: tuple[str, ...]
) -> str:
    """Bind exact displayed rows to their ledger sources before full verification.

    A binding is proposed by generation, never an approval of source support.
    No fuzzy matching, proximity inference, content rewriting or source pruning.
    """
    from ragkb.domain.citation_repair import citation_targets

    targets = citation_targets(answer)
    lines = answer.splitlines(keepends=True)
    for identity, line in targets.items():
        # Preserve every character except existing citation markers and outer
        # whitespace; in particular do not normalize units or punctuation.
        plain = re.sub(r"\[E\d+\]", "", line).strip()
        matches = [
            claim
            for claim, quote in zip(claims, quotes, strict=True)
            if quote.strip() == plain or claim.text.strip() == plain
        ]
        sources = list(dict.fromkeys(e for c in matches for e in c.evidence_ids))
        existing = set(re.findall(r"\[(E\d+)\]", line))
        sources = [e for e in sources if e not in existing]
        if not sources:
            continue
        index = int(identity[1:]) - 1
        original = lines[index]
        at = len(original.rstrip("\r\n"))
        if line.lstrip().startswith("|") and line.rstrip().endswith("|"):
            at = original.rfind("|", 0, at)
        lines[index] = original[:at] + " " + "".join(f"[{e}]" for e in sources) + original[at:]
    return "".join(lines)


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
