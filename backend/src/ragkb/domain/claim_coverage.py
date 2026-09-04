"""Deterministic, fail-closed coverage between answer prose and declared claims."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ragkb.domain.rag import AtomicClaim

_CLAUSE_BOUNDARY = re.compile(r"[。！？!?；;\n]+|(?<!\d)[，,](?!\d)")
_INLINE_CITATION = re.compile(r"[\[（(]\s*E\d+(?:\s*[,，]\s*E\d+)*\s*[\]）)]", re.IGNORECASE)
_DISCOURSE = frozenset(
    {
        "根据证据",
        "根据已验证证据",
        "依据证据",
        "答案如下",
        "同时",
        "并且",
        "而且",
        "以及",
        "另外",
        "因此",
        "其中",
        "and",
        "also",
        "basedontheevidence",
        "accordingtotheevidence",
        "theansweris",
    }
)
_CONNECTORS = (
    "根据已验证证据",
    "根据证据",
    "依据证据",
    "答案如下",
    "同时",
    "并且",
    "而且",
    "以及",
    "另外",
    "因此",
    "其中",
    "theansweris",
    "basedontheevidence",
    "accordingtotheevidence",
    "and",
    "also",
)


def _normalized(value: str) -> str:
    value = _INLINE_CITATION.sub("", unicodedata.normalize("NFKC", value).casefold())
    return "".join(character for character in value if character.isalnum() or character == "%")


def extract_answer_clauses(answer: str) -> tuple[str, ...]:
    clauses: list[str] = []
    for value in _CLAUSE_BOUNDARY.split(answer):
        clause = value.strip()
        normalized = _normalized(clause)
        if normalized and normalized not in _DISCOURSE:
            clauses.append(clause)
    return tuple(clauses)


@dataclass(frozen=True)
class AnswerClaimCoverage:
    clauses: tuple[str, ...]
    uncovered_clauses: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return bool(self.clauses) and not self.uncovered_clauses


def verify_answer_claim_coverage(
    answer: str, claims: tuple[AtomicClaim, ...]
) -> AnswerClaimCoverage:
    clauses = extract_answer_clauses(answer)
    claim_texts = tuple(filter(None, (_normalized(claim.text) for claim in claims)))
    uncovered: list[str] = []
    for clause in clauses:
        normalized_clause = _normalized(clause)
        if any(
            normalized_clause in claim_text or claim_text in normalized_clause
            for claim_text in claim_texts
        ):
            remainder = normalized_clause
            for claim_text in sorted(claim_texts, key=len, reverse=True):
                remainder = remainder.replace(claim_text, "", 1)
            for connector in _CONNECTORS:
                remainder = remainder.replace(_normalized(connector), "")
            if not remainder or any(normalized_clause in claim_text for claim_text in claim_texts):
                continue
        uncovered.append(clause)
    return AnswerClaimCoverage(clauses, tuple(uncovered))


def render_verified_claims(claims: tuple[AtomicClaim, ...]) -> str:
    """Build returned prose exclusively from claims that have passed verification."""

    rendered: list[str] = []
    seen: set[str] = set()
    for claim in claims:
        text = claim.text.strip()
        normalized = _normalized(text)
        if normalized and normalized not in seen:
            rendered.append(text)
            seen.add(normalized)
    return "\n".join(rendered)
