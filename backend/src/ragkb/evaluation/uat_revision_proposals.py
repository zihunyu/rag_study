"""Deterministic local-only proposal generation for one failed UAT candidate."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence

METHOD_REVISION = "uat-candidate-revision-terms:v1"
_ASCII_PATTERN = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_.-]{2,31}")
_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_SENSITIVE_PATTERN = re.compile(
    r"(?i)(api[-_]?key|access[-_]?key|secret|token|password|passwd|bearer|authorization|sk-[a-z0-9])"
)
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?:api[-_]?key|access[-_]?key|secret|token|password|passwd|authorization)"
    r"\s*[:=]\s*[^\s,，;；。]{1,128}"
)
_ASCII_STOPWORDS = frozenset(
    {
        "and",
        "are",
        "for",
        "from",
        "into",
        "that",
        "the",
        "this",
        "with",
    }
)
_CJK_STOPGRAMS = frozenset(
    {
        "一个",
        "以及",
        "使用",
        "关于",
        "其中",
        "可以",
        "如何",
        "对于",
        "进行",
        "这个",
        "这些",
    }
)


def _term_hash(term: str) -> str:
    return hashlib.sha256(term.encode(), usedforsecurity=False).hexdigest()


def _safe_term(term: str) -> bool:
    normalized = term.strip().casefold()
    return bool(
        normalized
        and not normalized.isdigit()
        and not _SENSITIVE_PATTERN.search(normalized)
        and normalized not in _ASCII_STOPWORDS
        and normalized not in _CJK_STOPGRAMS
    )


def _term_counts(text: str) -> Counter[str]:
    normalized = _SENSITIVE_ASSIGNMENT_PATTERN.sub(" ", unicodedata.normalize("NFKC", text))
    terms: list[str] = []
    for token in _ASCII_PATTERN.findall(normalized):
        lowered = token.casefold()
        if _safe_term(lowered):
            terms.append(lowered)
    for run in _CJK_PATTERN.findall(normalized):
        for size in (4, 3, 2):
            if len(run) < size:
                continue
            for start in range(len(run) - size + 1):
                term = run[start : start + size]
                if _safe_term(term):
                    terms.append(term)
    return Counter(terms)


def distinctive_positive_terms(
    positive: str, distractors: Sequence[str], *, limit: int = 8
) -> list[str]:
    positive_counts = _term_counts(positive)
    distractor_terms: set[str] = set()
    for distractor in distractors:
        distractor_terms.update(_term_counts(distractor))
    candidates = [term for term in positive_counts if term not in distractor_terms]
    candidates.sort(
        key=lambda term: (
            -len(term),
            -positive_counts[term],
            _term_hash(term),
        )
    )
    return candidates[:limit]


def _question_stem(question: str) -> str:
    return question.strip().rstrip("?？。.!！").strip()


def build_revision_proposals(review: Mapping[str, object]) -> dict[str, object]:
    question = review.get("question")
    candidate_id = review.get("candidate_id")
    documents = review.get("documents")
    if (
        not isinstance(question, str)
        or not question.strip()
        or not isinstance(candidate_id, str)
        or not candidate_id
        or not isinstance(documents, Sequence)
        or isinstance(documents, (str, bytes))
        or len(documents) != 4
    ):
        raise ValueError("UAT_REVISION_SOURCE_INVALID")
    mapped = [document for document in documents if isinstance(document, Mapping)]
    positives = [document for document in mapped if document.get("role") == "positive"]
    distractors = [document for document in mapped if document.get("role") == "distractor"]
    if len(mapped) != 4 or len(positives) != 1 or len(distractors) != 3:
        raise ValueError("UAT_REVISION_SOURCE_ROLES_INVALID")
    positive_content = positives[0].get("content")
    distractor_content = [document.get("content") for document in distractors]
    if not isinstance(positive_content, str) or any(
        not isinstance(content, str) for content in distractor_content
    ):
        raise ValueError("UAT_REVISION_SOURCE_CONTENT_INVALID")
    terms = distinctive_positive_terms(
        positive_content,
        [str(content) for content in distractor_content],
    )
    if not terms:
        raise ValueError("UAT_REVISION_DISTINCTIVE_TERMS_MISSING")
    while len(terms) < 3:
        terms.append(terms[-1])
    stem = _question_stem(question)
    term_sets = ((terms[0],), (terms[0], terms[1]), (terms[0], terms[2]))
    rendered = (
        f"请结合证据中“{terms[0]}”所在的具体内容回答：{stem}？",
        f"请聚焦“{terms[0]}”与“{terms[1]}”共同限定的内容回答：{stem}？",
        f"请仅依据涉及“{terms[0]}”和“{terms[2]}”的证据范围，具体说明：{stem}？",
    )
    if len(set(rendered)) != 3 or any(_SENSITIVE_PATTERN.search(value) for value in rendered):
        raise ValueError("UAT_REVISION_PROPOSAL_INVALID")
    proposals = [
        {
            "proposal_number": index,
            "question": proposal,
            "local_term_sha256": [_term_hash(term) for term in term_sets[index - 1]],
            "local_term_count": len(term_sets[index - 1]),
            "method_revision": METHOD_REVISION,
            "terms_from_positive_only": True,
            "evidence_external_facts_added": False,
        }
        for index, proposal in enumerate(rendered, start=1)
    ]
    return {
        "revision": "uat-candidate2-revision-proposals:v1",
        "method_revision": METHOD_REVISION,
        "candidate_id": candidate_id,
        "original_question": question,
        "proposal_count": 3,
        "proposals": proposals,
        "status": "PENDING_USER_REVIEW",
        "network_call_performed": False,
        "model_call_performed": False,
    }
