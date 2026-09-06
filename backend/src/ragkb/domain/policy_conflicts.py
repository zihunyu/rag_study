"""Conservative conflict checks over the full authorized retrieval evidence set.

Retrieval scores and authority_rank are not document precedence. Only currently
valid sources participate; unresolved overlapping policies cannot elect a winner
merely because one was retrieved or cited first. Semantic review handles paraphrases.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from itertools import combinations

from ragkb.domain.numeric_facts import NumericFact, extract_numeric_facts
from ragkb.domain.rag import AtomicClaim, Evidence

_SCOPES = re.compile(
    r"(?:适用于|适用范围(?:为|是|:|：)|适用对象(?:为|是|:|：)|仅限|仅对|针对)([^。；;\n]+)"
)
_POLARITY = {
    "不允许": ("允许", False),
    "允许": ("允许", True),
    "不可以": ("可以", False),
    "可以": ("可以", True),
    "不可": ("可", False),
    "可": ("可", True),
    "不支持": ("支持", False),
    "支持": ("支持", True),
    "不需要": ("需要", False),
    "无需": ("需要", False),
    "需要": ("需要", True),
    "不必": ("必须", False),
    "必须": ("必须", True),
    "不得": ("得", False),
    "应当": ("应当", True),
    "不应当": ("应当", False),
}
_MODALS = re.compile("|".join(sorted(_POLARITY, key=len, reverse=True)))


def _scope(evidence: Evidence) -> tuple[str, tuple[str, ...]]:
    text = unicodedata.normalize("NFKC", evidence.text)
    return (
        str(evidence.locator.get("section_path") or "root").strip(),
        tuple(re.sub(r"\s+", "", match[0]) for match in _SCOPES.finditer(text)),
    )


def _interval(fact: NumericFact) -> tuple[Decimal, bool, Decimal, bool]:
    value = fact.values[0]
    if fact.relation == "range":
        return value, True, fact.values[1], True
    if fact.relation in {"lt", "le"}:
        return Decimal("-Infinity"), False, value, fact.relation == "le"
    if fact.relation in {"gt", "ge"}:
        return value, fact.relation == "ge", Decimal("Infinity"), False
    return value, True, value, True


def _disjoint(left: NumericFact, right: NumericFact) -> bool:
    low_a, closed_low_a, high_a, closed_high_a = _interval(left)
    low_b, closed_low_b, high_b, closed_high_b = _interval(right)
    return (
        high_a < low_b
        or high_b < low_a
        or (high_a == low_b and not (closed_high_a and closed_low_b))
        or (high_b == low_a and not (closed_high_b and closed_low_a))
    )


def _polarity_facts(text: str) -> dict[str, tuple[bool, ...]]:
    result: dict[str, tuple[bool, ...]] = {}
    for clause in re.split(r"[。；;\n]", unicodedata.normalize("NFKC", text)):
        matches = tuple(_MODALS.finditer(clause))
        if matches:
            skeleton = _MODALS.sub(lambda m: "{" + _POLARITY[m[0]][0] + "}", clause)
            result[re.sub(r"\s+", "", skeleton)] = tuple(_POLARITY[m[0]][1] for m in matches)
    return result


def conflicting_sources(
    evidence: tuple[Evidence, ...], *, at_epoch: int, claims: tuple[AtomicClaim, ...] | None = None
) -> tuple[str, ...]:
    active = tuple(
        e for e in evidence if e.authorized and e.current_version and e.valid_at(at_epoch)
    )
    parsed = {e.evidence_id: extract_numeric_facts(e.text) for e in active}
    polarities = {e.evidence_id: _polarity_facts(e.text) for e in active}
    scopes = {e.evidence_id: _scope(e) for e in active}
    subjects = {
        fact.subject for claim in claims or () for fact in extract_numeric_facts(claim.text).facts
    }
    claim_polarities = {key for claim in claims or () for key in _polarity_facts(claim.text)}
    conflicts: set[str] = set()
    for left, right in combinations(active, 2):
        if scopes[left.evidence_id] != scopes[right.evidence_id]:
            continue
        numeric_conflict = any(
            a.certain
            and b.certain
            and a.subject != ("", "")
            and a.subject == b.subject
            and (claims is None or a.subject in subjects)
            and a.unit == b.unit
            and _disjoint(a, b)
            for a in parsed[left.evidence_id].facts
            for b in parsed[right.evidence_id].facts
        )
        left_polarity, right_polarity = polarities[left.evidence_id], polarities[right.evidence_id]
        polarity_conflict = any(
            left_polarity[key] != right_polarity[key]
            and (claims is None or key in claim_polarities)
            for key in left_polarity.keys() & right_polarity.keys()
        )
        if numeric_conflict or polarity_conflict:
            conflicts.update((left.evidence_id, right.evidence_id))
    return tuple(e.evidence_id for e in active if e.evidence_id in conflicts)
