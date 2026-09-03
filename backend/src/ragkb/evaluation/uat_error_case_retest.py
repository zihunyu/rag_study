"""Dynamic, future-only preparation for user-authorized UAT error-case retests."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from ragkb.evaluation.uat_generic_remediation import (
    UatRemediationError,
    build_evidence_envelope,
    canonical_sha256,
    text_sha256,
)

_RETEST_VERDICTS = frozenset({"不通过", "待修订"})


def select_retest_case_ids(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """Select only review rows whose verdict requires a generic retest."""

    selected = {
        candidate_id
        for row in rows
        if row.get("type") == "candidate_review"
        and row.get("audit_verdict") in _RETEST_VERDICTS
        and isinstance((candidate_id := row.get("candidate_id")), str)
    }
    if not selected:
        raise UatRemediationError("UAT_RETEST_SELECTION_EMPTY")
    return sorted(selected)


def prepare_retest_cases(
    selected_case_ids: Sequence[str],
    fresh_sources: Mapping[str, Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    """Turn fresh source evidence into eligible future cases or local blocked records."""

    if len(set(selected_case_ids)) != len(selected_case_ids):
        raise UatRemediationError("UAT_RETEST_SELECTION_DUPLICATE")
    eligible: list[dict[str, object]] = []
    blocked: list[dict[str, object]] = []
    for selected_id in sorted(selected_case_ids):
        source = fresh_sources.get(selected_id)
        if not isinstance(source, Mapping):
            raise UatRemediationError("UAT_RETEST_FRESH_SOURCE_MISSING")
        question = source.get("question")
        raw_evidence = source.get("evidence")
        allow_cross_document = source.get("allow_cross_document", False)
        source_classification = source.get("source_classification")
        if (
            not isinstance(question, str)
            or not isinstance(raw_evidence, Mapping)
            or not isinstance(allow_cross_document, bool)
            or not isinstance(source_classification, str)
            or not source_classification
        ):
            raise UatRemediationError("UAT_RETEST_FRESH_SOURCE_SCHEMA_INVALID")
        test_case_id = hashlib.sha256(
            f"future-error-retest:{selected_id}:{text_sha256(question)}".encode(),
            usedforsecurity=False,
        ).hexdigest()[:24]
        try:
            requires_rendered_proof = raw_evidence.get("requires_rendered_proof")
            if not isinstance(requires_rendered_proof, bool):
                raise UatRemediationError("UAT_RETEST_RENDER_PROOF_POLICY_INVALID")
            if requires_rendered_proof and not isinstance(raw_evidence.get("rendered_text"), str):
                raise UatRemediationError("UAT_RETEST_RENDER_PROOF_REQUIRED")
            envelope = build_evidence_envelope(
                evidence_id=str(raw_evidence["evidence_id"]),
                source_document_id=str(raw_evidence["source_document_id"]),
                source_version_sha256=str(raw_evidence["source_version_sha256"]),
                content=str(raw_evidence["content"]),
                locator=raw_evidence["locator"],
                entity_id=raw_evidence.get("entity_id"),
                field_key=raw_evidence.get("field_key"),
                rendered_text=raw_evidence.get("rendered_text"),
                render_proof=raw_evidence.get("render_proof"),
            )
        except (KeyError, TypeError, UatRemediationError) as error:
            blocked.append(
                {
                    "revision": "uat-error-retest-preflight:v1",
                    "test_case_id": test_case_id,
                    "selected_case_sha256": text_sha256(selected_id),
                    "question_sha256": text_sha256(question),
                    "source_classification": source_classification,
                    "state": "BLOCKED",
                    "reason_code": str(error),
                    "provider_call_count": 0,
                    "content_output": False,
                }
            )
            continue
        eligible.append(
            {
                "test_case_id": test_case_id,
                "question": question,
                "allow_cross_document": allow_cross_document,
                "source_classification": source_classification,
                "evidence": [
                    {
                        "evidence_id": envelope["evidence_id"],
                        "source_document_id": envelope["source_document_id"],
                        "source_version_sha256": envelope["source_version_sha256"],
                        "content": envelope["content"],
                        "locator": envelope["locator"],
                        "entity_id": envelope["entity_id"],
                        "field_key": envelope["field_key"],
                        "rendered_text": raw_evidence.get("rendered_text"),
                        "render_proof": raw_evidence.get("render_proof"),
                    }
                ],
            }
        )
    manifest = {
        "revision": "uat-error-retest-preflight-manifest:v1",
        "selected_case_count": len(selected_case_ids),
        "eligible_case_count": len(eligible),
        "blocked_case_count": len(blocked),
        "eligible_case_snapshot_sha256": canonical_sha256(
            [
                {
                    "test_case_id": item["test_case_id"],
                    "question_sha256": text_sha256(str(item["question"])),
                    "evidence_count": 1,
                    "source_classification": item["source_classification"],
                }
                for item in eligible
            ]
        ),
        "blocked_case_snapshot_sha256": canonical_sha256(blocked),
        "provider_call_count": 0,
        "content_output": False,
    }
    return eligible, blocked, manifest
