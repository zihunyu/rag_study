"""Freeze candidate 2 proposal 1 into a one-request diagnostic bundle."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping, Sequence


def _derived_candidate_id(original_candidate_id: str, proposal_sha256: str) -> str:
    return hashlib.sha256(
        f"{original_candidate_id}:proposal-1:{proposal_sha256}:diagnostic-v2".encode(),
        usedforsecurity=False,
    ).hexdigest()[:20]


def build_candidate2_diagnostic_v2(
    proposals: Mapping[str, object],
    failure_review: Mapping[str, object],
    original_bundle: Mapping[str, object],
    *,
    proposal_sha256: str,
    failure_review_sha256: str,
    reranker_v1_sha256: str,
    original_bundle_sha256: str,
) -> tuple[dict[str, object], dict[str, object]]:
    original_candidate_id = failure_review.get("candidate_id")
    proposal_items = proposals.get("proposals")
    review_documents = failure_review.get("documents")
    bundle_documents = original_bundle.get("documents")
    if (
        not isinstance(original_candidate_id, str)
        or proposals.get("candidate_id") != original_candidate_id
        or original_bundle.get("candidate_id") != original_candidate_id
        or not isinstance(proposal_items, Sequence)
        or isinstance(proposal_items, (str, bytes))
        or len(proposal_items) != 3
        or not isinstance(review_documents, Sequence)
        or isinstance(review_documents, (str, bytes))
        or not isinstance(bundle_documents, Sequence)
        or isinstance(bundle_documents, (str, bytes))
        or len(review_documents) != 4
        or len(bundle_documents) != 4
    ):
        raise ValueError("UAT_DIAGNOSTIC_V2_SOURCE_INVALID")
    proposal_one = next(
        (
            proposal
            for proposal in proposal_items
            if isinstance(proposal, Mapping) and proposal.get("proposal_number") == 1
        ),
        None,
    )
    if proposal_one is None or not isinstance(proposal_one.get("question"), str):
        raise ValueError("UAT_DIAGNOSTIC_V2_PROPOSAL_1_MISSING")
    if failure_review.get("question") != original_bundle.get("question"):
        raise ValueError("UAT_DIAGNOSTIC_V2_ORIGINAL_QUESTION_MISMATCH")
    projected_bundle_documents = [
        {
            "evidence_id": document.get("evidence_id"),
            "role": document.get("role"),
            "locator": document.get("locator"),
            "content": document.get("content"),
            "content_sha256": document.get("content_sha256"),
        }
        for document in bundle_documents
        if isinstance(document, Mapping)
    ]
    if list(review_documents) != projected_bundle_documents:
        raise ValueError("UAT_DIAGNOSTIC_V2_DOCUMENTS_MISMATCH")
    derived_id = _derived_candidate_id(original_candidate_id, proposal_sha256)
    manifest = {
        "original_candidate_id": original_candidate_id,
        "proposal_number": 1,
        "proposal_artifact_sha256": proposal_sha256,
        "failure_review_sha256": failure_review_sha256,
        "reranker_v1_checkpoint_sha256": reranker_v1_sha256,
        "original_bundle_sha256": original_bundle_sha256,
        "user_decision": "SELECT_PROPOSAL_1",
        "authorized_action": "ONE_RERANKER_V2_DIAGNOSTIC_RETRY_ZERO",
    }
    revision = {
        "revision": "uat-candidate2-revision:v2",
        "candidate_id": derived_id,
        "original_candidate_id": original_candidate_id,
        "candidate_revision": 2,
        "question": proposal_one["question"],
        "proposal_number": 1,
        "manifest": manifest,
        "status": "APPROVED_FOR_SINGLE_RERANKER_V2_DIAGNOSTIC",
    }
    bundle = {
        "revision": "locator-grounded-uat-diagnostic-bundle:v2",
        "candidate_id": derived_id,
        "original_candidate_id": original_candidate_id,
        "candidate_revision": 2,
        "question": proposal_one["question"],
        "source_category": original_bundle.get("source_category"),
        "source_classification": original_bundle.get("source_classification"),
        "expected_locator": copy.deepcopy(original_bundle.get("expected_locator")),
        "expected_evidence": copy.deepcopy(original_bundle.get("expected_evidence")),
        "expected_positive_evidence_id": original_bundle.get("expected_positive_evidence_id"),
        "documents": copy.deepcopy(list(bundle_documents)),
        "document_count": 4,
        "manifest": manifest,
        "diagnostic_only": True,
        "max_requests": 1,
        "positive_top_k": 2,
        "automatic_retries": 0,
        "llm_allowed": False,
        "query_embedding_request_count": 0,
        "zilliz_request_count": 0,
    }
    return revision, bundle
