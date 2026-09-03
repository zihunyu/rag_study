"""All-or-nothing deterministic positive-only revisions for UAT candidates 4-78."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence

METHOD_REVISION = "uat-systematic-positive-terms:v4"
_ASCII = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_.-]{2,31}")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_SENSITIVE = re.compile(
    r"(?i)(api[-_]?key|access[-_]?key|secret|token|password|passwd|bearer|authorization|sk-[a-z0-9])"
)
_ASSIGNMENT = re.compile(
    r"(?i)(?:api[-_]?key|access[-_]?key|secret|token|password|passwd|authorization)"
    r"\s*[:=]\s*[^\s,，;；。]{1,128}"
)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(),
        usedforsecurity=False,
    ).hexdigest()


def _term_hash(term: str) -> str:
    return hashlib.sha256(term.encode(), usedforsecurity=False).hexdigest()


def _terms_v4(text: str) -> Counter[str]:
    normalized = _ASSIGNMENT.sub(" ", unicodedata.normalize("NFKC", text))
    terms: list[str] = []
    ascii_tokens = [token.casefold() for token in _ASCII.findall(normalized)]
    for token in ascii_tokens:
        if not _SENSITIVE.search(token) and not token.isdigit():
            terms.append(token)
    for size in (4, 3, 2):
        for start in range(len(ascii_tokens) - size + 1):
            phrase = " ".join(ascii_tokens[start : start + size])
            if not _SENSITIVE.search(phrase):
                terms.append(phrase)
    for run in _CJK.findall(normalized):
        for size in range(min(12, len(run)), 1, -1):
            for start in range(len(run) - size + 1):
                term = run[start : start + size]
                if not _SENSITIVE.search(term):
                    terms.append(term)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    for left, right in zip(lines, lines[1:], strict=False):
        for size in (12, 8, 4):
            if len(left) >= size and len(right) >= size:
                boundary = f"{left[-size:]}\n{right[:size]}"
                if not _SENSITIVE.search(boundary):
                    terms.append(boundary)
    for window_size in (4, 3):
        for start in range(len(lines) - window_size + 1):
            window = lines[start : start + window_size]
            boundary = "\n".join([window[0][-8:], *window[1:-1], window[-1][:8]])
            if len(boundary) <= 160 and not _SENSITIVE.search(boundary):
                terms.append(boundary)
    return Counter(terms)


def distinctive_positive_terms_v4(
    positive: str, distractors: Sequence[str], *, limit: int = 8
) -> list[str]:
    positive_counts = _terms_v4(positive)
    distractor_terms: set[str] = set()
    for distractor in distractors:
        distractor_terms.update(_terms_v4(distractor))
    candidates = [term for term in positive_counts if term not in distractor_terms]
    preferred = [term for term in candidates if len(term) >= 4]
    selected = preferred or candidates
    selected.sort(key=lambda term: (len(term), -positive_counts[term], _term_hash(term)))
    return selected[:limit]


def _recommended_question(
    original_question: str, positive: str, distractors: Sequence[str]
) -> tuple[str, list[str]]:
    terms = distinctive_positive_terms_v4(positive, distractors)
    if not terms:
        raise ValueError("UAT_REVISION_DISTINCTIVE_TERMS_MISSING")
    stem = original_question.strip().rstrip("?？。.!！").strip()
    question = f"请结合证据中“{terms[0]}”所在的具体内容回答：{stem}？"
    if _SENSITIVE.search(question):
        raise ValueError("UAT_SYSTEMATIC_REVISION_V4_PROPOSAL_INVALID")
    return question, [_term_hash(terms[0])]


def build_systematic_revision_v4(
    bundles: Sequence[Mapping[str, object]],
    source_records: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], dict[str, dict[str, object]], dict[str, object]]:
    if len(bundles) != 75 or len(source_records) != 75:
        raise ValueError("UAT_SYSTEMATIC_REVISION_V4_COUNT_INVALID")
    revisions: list[dict[str, object]] = []
    revised_bundles: dict[str, dict[str, object]] = {}
    categories: Counter[str] = Counter()
    for position, (bundle, source_record) in enumerate(
        zip(bundles, source_records, strict=True), start=4
    ):
        original_id = bundle.get("candidate_id")
        documents = bundle.get("documents")
        if (
            not isinstance(original_id, str)
            or source_record.get("candidate_id") != original_id
            or source_record.get("position") != position
            or not isinstance(documents, Sequence)
            or isinstance(documents, (str, bytes))
            or len(documents) != 4
        ):
            raise ValueError("UAT_SYSTEMATIC_REVISION_V4_SOURCE_INVALID")
        mapped_documents = [document for document in documents if isinstance(document, Mapping)]
        positives = [
            document for document in mapped_documents if document.get("role") == "positive"
        ]
        distractors = [
            document for document in mapped_documents if document.get("role") == "distractor"
        ]
        original_question = bundle.get("question")
        if (
            len(mapped_documents) != 4
            or len(positives) != 1
            or len(distractors) != 3
            or not isinstance(original_question, str)
            or not isinstance(positives[0].get("content"), str)
            or any(not isinstance(document.get("content"), str) for document in distractors)
        ):
            raise ValueError("UAT_SYSTEMATIC_REVISION_V4_DOCUMENTS_INVALID")
        revised_question, term_hashes = _recommended_question(
            original_question,
            str(positives[0]["content"]),
            [str(document["content"]) for document in distractors],
        )
        revision_id = hashlib.sha256(
            f"{original_id}:systematic-revision-v4:{_hash(revised_question)}".encode(),
            usedforsecurity=False,
        ).hexdigest()[:20]
        evidence_hash = _hash(documents)
        source_category = str(bundle.get("source_category"))
        categories[source_category] += 1
        revised_bundle = copy.deepcopy(dict(bundle))
        revised_bundle.update(
            revision="locator-grounded-uat-systematic-bundle:v4",
            candidate_id=revision_id,
            original_candidate_id=original_id,
            candidate_revision=4,
            question=revised_question,
            evidence_unchanged_sha256=evidence_hash,
            user_review_status="PENDING_USER_REVIEW",
        )
        revised_bundles[revision_id] = revised_bundle
        revised_bundle_payload = (
            json.dumps(
                revised_bundle,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode()
        revisions.append(
            {
                "position": position,
                "original_candidate_id": original_id,
                "revision_candidate_id": revision_id,
                "source_category": source_category,
                "source_bundle_ref": source_record.get("bundle_ref"),
                "source_bundle_sha256": source_record.get("bundle_sha256"),
                "original_question": bundle.get("question"),
                "revised_question": revised_question,
                "local_term_sha256": term_hashes,
                "method_revision": METHOD_REVISION,
                "terms_from_positive_only": True,
                "evidence_external_facts_added": False,
                "evidence_unchanged_sha256": evidence_hash,
                "evidence_document_count": 4,
                "revision_bundle_ref": (f"uat-systematic-revision-v4/bundles/{revision_id}.json"),
                "revision_bundle_sha256": hashlib.sha256(
                    revised_bundle_payload, usedforsecurity=False
                ).hexdigest(),
                "status": "PENDING_USER_REVIEW",
            }
        )
    if len(revised_bundles) != 75:
        raise ValueError("UAT_SYSTEMATIC_REVISION_V4_ID_DUPLICATE")
    category_counts = dict(sorted(categories.items()))
    review = {
        "revision": "uat-systematic-revision-review:v4",
        "candidate_count": 75,
        "positions": "4-78",
        "method_revision": METHOD_REVISION,
        "status": "PENDING_USER_REVIEW",
        "revisions": revisions,
        "category_counts": category_counts,
        "model_call_performed": False,
        "network_call_performed": False,
    }
    manifest_base = {
        "revision": "uat-systematic-revision-manifest:v4",
        "candidate_count": 75,
        "positions": "4-78",
        "status": "PENDING_USER_REVIEW",
        "category_counts": category_counts,
        "source_bundle_snapshot_sha256": _hash(list(source_records)),
        "automatic_retries": 0,
        "model_call_performed": False,
        "network_call_performed": False,
    }
    return review, revised_bundles, manifest_base
