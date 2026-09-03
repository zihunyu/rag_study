"""Two-term positive-only systematic revisions for the remaining 39 UAT cases."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from itertools import combinations

from ragkb.evaluation.uat_systematic_revision import distinctive_positive_terms_v4

METHOD_REVISION = "uat-systematic-two-positive-terms:v5"
_ASCII_FAMILY = re.compile(r"^[a-z0-9_. -]+$", re.IGNORECASE)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(),
        usedforsecurity=False,
    ).hexdigest()


def _term_hash(term: str) -> str:
    return hashlib.sha256(term.encode(), usedforsecurity=False).hexdigest()


def _family(term: str) -> str:
    if "\n" in term:
        return "ordered_boundary"
    if _ASCII_FAMILY.fullmatch(term):
        return "ascii"
    return "cjk_or_mixed"


def select_two_positive_terms(positive: str, distractors: Sequence[str]) -> tuple[str, str]:
    terms = distinctive_positive_terms_v4(positive, distractors, limit=64)
    if len(terms) < 2:
        raise ValueError("UAT_SYSTEMATIC_V5_TWO_DISTINCTIVE_TERMS_MISSING")

    def overlap(first: str, second: str) -> bool:
        normalized_first = first.casefold()
        normalized_second = second.casefold()
        return normalized_first in normalized_second or normalized_second in normalized_first

    pairs = list(combinations(terms, 2))
    pairs.sort(
        key=lambda pair: (
            _family(pair[0]) == _family(pair[1]),
            overlap(pair[0], pair[1]),
            max(len(pair[0]), len(pair[1])),
            len(pair[0]) + len(pair[1]),
            _term_hash(pair[0]),
            _term_hash(pair[1]),
        )
    )
    return pairs[0]


def build_systematic_revision_v5(
    bundles: Sequence[Mapping[str, object]],
    source_records: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], dict[str, dict[str, object]], dict[str, object]]:
    if len(bundles) != 39 or len(source_records) != 39:
        raise ValueError("UAT_SYSTEMATIC_REVISION_V5_COUNT_INVALID")
    revisions: list[dict[str, object]] = []
    revised_bundles: dict[str, dict[str, object]] = {}
    categories: Counter[str] = Counter()
    for position, (bundle, source_record) in enumerate(
        zip(bundles, source_records, strict=True), start=40
    ):
        source_candidate_id = bundle.get("candidate_id")
        documents = bundle.get("documents")
        if (
            not isinstance(source_candidate_id, str)
            or source_record.get("candidate_id") != source_candidate_id
            or source_record.get("position") != position
            or not isinstance(documents, Sequence)
            or isinstance(documents, (str, bytes))
            or len(documents) != 4
        ):
            raise ValueError("UAT_SYSTEMATIC_REVISION_V5_SOURCE_INVALID")
        mapped = [document for document in documents if isinstance(document, Mapping)]
        positives = [document for document in mapped if document.get("role") == "positive"]
        distractors = [document for document in mapped if document.get("role") == "distractor"]
        original_question = bundle.get("question")
        if (
            len(mapped) != 4
            or len(positives) != 1
            or len(distractors) != 3
            or not isinstance(original_question, str)
            or not isinstance(positives[0].get("content"), str)
            or any(not isinstance(document.get("content"), str) for document in distractors)
        ):
            raise ValueError("UAT_SYSTEMATIC_REVISION_V5_DOCUMENTS_INVALID")
        first_term, second_term = select_two_positive_terms(
            str(positives[0]["content"]),
            [str(document["content"]) for document in distractors],
        )
        stem = original_question.strip().rstrip("?？。.!！").strip()
        revised_question = (
            f"请结合证据中“{first_term}”和“{second_term}”所在的具体内容回答：{stem}？"
        )
        original_candidate_id = str(bundle.get("original_candidate_id", source_candidate_id))
        revision_id = hashlib.sha256(
            (
                f"{original_candidate_id}:{source_candidate_id}:"
                f"systematic-revision-v5:{_hash(revised_question)}"
            ).encode(),
            usedforsecurity=False,
        ).hexdigest()[:20]
        evidence_hash = _hash(documents)
        category = str(bundle.get("source_category"))
        categories[category] += 1
        revised_bundle = copy.deepcopy(dict(bundle))
        revised_bundle.update(
            revision="locator-grounded-uat-systematic-bundle:v5",
            candidate_id=revision_id,
            original_candidate_id=original_candidate_id,
            source_revision_candidate_id=source_candidate_id,
            candidate_revision=5,
            question=revised_question,
            evidence_unchanged_sha256=evidence_hash,
            user_review_status="PENDING_USER_REVIEW",
        )
        revised_bundles[revision_id] = revised_bundle
        payload = (
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
                "original_candidate_id": original_candidate_id,
                "source_revision_candidate_id": source_candidate_id,
                "revision_candidate_id": revision_id,
                "source_category": category,
                "source_bundle_ref": source_record.get("bundle_ref"),
                "source_bundle_sha256": source_record.get("bundle_sha256"),
                "original_question": original_question,
                "revised_question": revised_question,
                "local_term_sha256": [_term_hash(first_term), _term_hash(second_term)],
                "term_families": [_family(first_term), _family(second_term)],
                "terms_non_overlapping": not (
                    first_term.casefold() in second_term.casefold()
                    or second_term.casefold() in first_term.casefold()
                ),
                "method_revision": METHOD_REVISION,
                "terms_from_positive_only": True,
                "evidence_external_facts_added": False,
                "evidence_unchanged_sha256": evidence_hash,
                "evidence_document_count": 4,
                "revision_bundle_ref": (f"uat-systematic-revision-v5/bundles/{revision_id}.json"),
                "revision_bundle_sha256": hashlib.sha256(
                    payload, usedforsecurity=False
                ).hexdigest(),
                "status": "PENDING_USER_REVIEW",
            }
        )
    if len(revised_bundles) != 39:
        raise ValueError("UAT_SYSTEMATIC_REVISION_V5_ID_DUPLICATE")
    category_counts = dict(sorted(categories.items()))
    review = {
        "revision": "uat-systematic-revision-review:v5",
        "candidate_count": 39,
        "positions": "40-78",
        "method_revision": METHOD_REVISION,
        "status": "PENDING_USER_REVIEW",
        "revisions": revisions,
        "category_counts": category_counts,
        "model_call_performed": False,
        "network_call_performed": False,
    }
    manifest = {
        "revision": "uat-systematic-revision-manifest:v5",
        "candidate_count": 39,
        "positions": "40-78",
        "status": "PENDING_USER_REVIEW",
        "category_counts": category_counts,
        "source_bundle_snapshot_sha256": _hash(list(source_records)),
        "automatic_retries": 0,
        "model_call_performed": False,
        "network_call_performed": False,
    }
    return review, revised_bundles, manifest
