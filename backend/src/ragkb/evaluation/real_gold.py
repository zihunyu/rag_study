"""Business-reviewed ten-case Gold Dataset contract for low-cost real acceptance."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from typing import Any

REQUIRED_QUERY_TYPES = frozenset(
    {
        "identifier",
        "keyword",
        "semantic",
        "multihop",
        "temporal",
        "negative",
        "unanswerable",
        "permission",
    }
)


def canonical_gold_payload(dataset: Mapping[str, Any]) -> bytes:
    body = {key: value for key, value in dataset.items() if key != "signature"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sign_gold_dataset(dataset: Mapping[str, Any], key: bytes) -> str:
    if len(key) < 16:
        raise ValueError("GOLD_SIGNING_KEY_TOO_SHORT")
    return hmac.new(key, canonical_gold_payload(dataset), hashlib.sha256).hexdigest()


def validate_real_gold_dataset(
    dataset: Mapping[str, Any], key: bytes, *, required_cases: int = 10
) -> dict[str, object]:
    if dataset.get("schema_version") != 2 or dataset.get("status") != "APPROVED":
        raise ValueError("REAL_GOLD_NOT_BUSINESS_APPROVED")
    reviewer = str(dataset.get("reviewer_id", "")).strip()
    reviewed_at = str(dataset.get("reviewed_at", "")).strip()
    if not reviewer or not reviewed_at:
        raise ValueError("REAL_GOLD_REVIEW_IDENTITY_REQUIRED")
    cases = dataset.get("cases")
    corpus = dataset.get("corpus")
    if not isinstance(corpus, Sequence) or isinstance(corpus, (str, bytes)) or len(corpus) != 20:
        raise ValueError("REAL_GOLD_CORPUS_EXACTLY_TWENTY_CHUNKS_REQUIRED")
    corpus_ids: list[str] = []
    for item in corpus:
        if not isinstance(item, Mapping) or not {
            "chunk_id",
            "document_id",
            "document_version_id",
            "text",
            "locator",
        }.issubset(item):
            raise ValueError("REAL_GOLD_CORPUS_CHUNK_INVALID")
        chunk_id = str(item["chunk_id"])
        if not chunk_id or chunk_id in corpus_ids or not str(item["text"]).strip():
            raise ValueError("REAL_GOLD_CORPUS_CHUNK_INVALID")
        corpus_ids.append(chunk_id)
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)):
        raise ValueError("REAL_GOLD_CASES_INVALID")
    if len(cases) != required_cases:
        raise ValueError("REAL_GOLD_EXACTLY_TEN_CASES_REQUIRED")
    case_ids: set[str] = set()
    query_types: set[str] = set()
    performance_scales: set[int] = set()
    for raw in cases:
        if not isinstance(raw, Mapping):
            raise ValueError("REAL_GOLD_CASE_INVALID")
        required = {
            "case_id",
            "query_type",
            "question",
            "expected_answer",
            "allowed_evidence_ids",
            "forbidden_evidence_ids",
            "principal",
            "expected_status",
            "performance_scale",
        }
        if not required.issubset(raw):
            raise ValueError("REAL_GOLD_CASE_FIELDS_MISSING")
        case_id = str(raw["case_id"]).strip()
        query_type = str(raw["query_type"]).strip()
        if not case_id or case_id in case_ids:
            raise ValueError("REAL_GOLD_CASE_ID_INVALID")
        if query_type not in REQUIRED_QUERY_TYPES:
            raise ValueError("REAL_GOLD_QUERY_TYPE_INVALID")
        if not str(raw["question"]).strip() or str(raw["expected_status"]) not in {
            "answered",
            "insufficient_evidence",
            "out_of_scope",
        }:
            raise ValueError("REAL_GOLD_CASE_CONTENT_MISSING")
        scale = int(raw["performance_scale"])
        if scale not in {1, 5, 20}:
            raise ValueError("REAL_GOLD_PERFORMANCE_SCALE_INVALID")
        allowed = set(map(str, raw["allowed_evidence_ids"]))
        if not allowed.issubset(corpus_ids[:scale]):
            raise ValueError("REAL_GOLD_EVIDENCE_OUTSIDE_PERFORMANCE_SCALE")
        principal = raw["principal"]
        if not isinstance(principal, Mapping) or not {
            "tenant_id",
            "user_id",
            "clearance_level",
            "scope_tokens",
        }.issubset(principal):
            raise ValueError("REAL_GOLD_PRINCIPAL_INVALID")
        case_ids.add(case_id)
        query_types.add(query_type)
        performance_scales.add(scale)
    if not REQUIRED_QUERY_TYPES.issubset(query_types):
        raise ValueError("REAL_GOLD_QUERY_TYPE_COVERAGE_INCOMPLETE")
    if performance_scales != {1, 5, 20}:
        raise ValueError("REAL_GOLD_PERFORMANCE_SCALE_COVERAGE_INCOMPLETE")
    signature = str(dataset.get("signature", ""))
    expected = sign_gold_dataset(dataset, key)
    if not signature or not hmac.compare_digest(signature, expected):
        raise ValueError("REAL_GOLD_SIGNATURE_INVALID")
    return {
        "dataset_id": str(dataset.get("dataset_id", "")),
        "dataset_revision": str(dataset.get("revision", "")),
        "case_count": len(cases),
        "query_types": sorted(query_types),
        "performance_scope": sorted(performance_scales),
        "reviewer_id": reviewer,
        "business_approved": True,
        "signature_valid": True,
        "starter_dataset_accepted": False,
    }
