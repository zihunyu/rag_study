"""Versioned, content-neutral integrity rules for future UAT submissions.

This module deliberately accepts arbitrary evidence and identifiers.  It contains no
knowledge-base-specific values and does not read or alter historical UAT artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

_ALLOWED_STATUSES = frozenset(
    {"answered", "insufficient_evidence", "needs_clarification", "conflicting_evidence"}
)
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_SAFE_CONTROL = frozenset({"\n", "\r", "\t"})


class UatRemediationError(ValueError):
    """A generic provenance, claim, state, or source-integrity violation."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value), usedforsecurity=False).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode(), usedforsecurity=False).hexdigest()


def _safe_identifier(value: object, code: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise UatRemediationError(code)
    return value


def _optional_identifier(value: object, code: str) -> str | None:
    return _safe_identifier(value, code) if value is not None else None


def _sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or not _HEX_SHA256.fullmatch(value):
        raise UatRemediationError(code)
    return value


def _normalized_source_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def validate_source_integrity(
    source_text: str, *, rendered_text: str | None = None
) -> dict[str, object]:
    """Reject corrupted text before it is admitted to an evidence pool."""

    if not isinstance(source_text, str) or not source_text.strip():
        raise UatRemediationError("UAT_SOURCE_TEXT_EMPTY")
    if "\ufffd" in source_text or any(
        unicodedata.category(character) == "Cc" and character not in _SAFE_CONTROL
        for character in source_text
    ):
        raise UatRemediationError("UAT_SOURCE_TEXT_CONTROL_OR_REPLACEMENT")
    normalized = _normalized_source_text(source_text)
    if not normalized:
        raise UatRemediationError("UAT_SOURCE_TEXT_EMPTY")
    result: dict[str, object] = {
        "source_text_sha256": text_sha256(source_text),
        "normalized_text_sha256": text_sha256(normalized),
        "rendered_text_verified": False,
    }
    if rendered_text is not None:
        if not isinstance(rendered_text, str) or not rendered_text.strip():
            raise UatRemediationError("UAT_SOURCE_RENDERED_TEXT_EMPTY")
        if "\ufffd" in rendered_text or any(
            unicodedata.category(character) == "Cc" and character not in _SAFE_CONTROL
            for character in rendered_text
        ):
            raise UatRemediationError("UAT_SOURCE_RENDERED_TEXT_CONTROL_OR_REPLACEMENT")
        if _normalized_source_text(rendered_text) != normalized:
            raise UatRemediationError("UAT_SOURCE_RENDERED_TEXT_MISMATCH")
        result["rendered_text_verified"] = True
        result["rendered_text_sha256"] = text_sha256(rendered_text)
    return result


def build_evidence_envelope(
    *,
    evidence_id: str,
    source_document_id: str,
    source_version_sha256: str,
    content: str,
    locator: Mapping[str, object],
    entity_id: str | None = None,
    field_key: str | None = None,
    rendered_text: str | None = None,
    render_proof: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Create one immutable, claim-addressable evidence envelope."""

    _safe_identifier(evidence_id, "UAT_EVIDENCE_ID_INVALID")
    _safe_identifier(source_document_id, "UAT_SOURCE_DOCUMENT_ID_INVALID")
    _sha256(source_version_sha256, "UAT_SOURCE_VERSION_HASH_INVALID")
    if not isinstance(locator, Mapping) or not locator:
        raise UatRemediationError("UAT_EVIDENCE_LOCATOR_INVALID")
    integrity = validate_source_integrity(content, rendered_text=rendered_text)
    if rendered_text is not None and (
        not isinstance(render_proof, Mapping)
        or render_proof.get("revision") != "uat-independent-render-proof:v1"
        or render_proof.get("source_version_sha256") != source_version_sha256
        or not isinstance(render_proof.get("locator_sha256"), str)
        or not isinstance(render_proof.get("representation_sha256"), str)
    ):
        raise UatRemediationError("UAT_RENDER_PROOF_INVALID")
    normalized_entity = (
        _safe_identifier(entity_id, "UAT_EVIDENCE_ENTITY_ID_INVALID")
        if entity_id is not None
        else None
    )
    normalized_field = (
        _safe_identifier(field_key, "UAT_EVIDENCE_FIELD_KEY_INVALID")
        if field_key is not None
        else None
    )
    return {
        "revision": "uat-evidence-envelope:v1",
        "evidence_id": evidence_id,
        "source_document_id": source_document_id,
        "source_version_sha256": source_version_sha256,
        "content": content,
        "content_sha256": text_sha256(content),
        "locator": dict(locator),
        "locator_sha256": canonical_sha256(dict(locator)),
        "entity_id": normalized_entity,
        "field_key": normalized_field,
        "evidence_span_sha256": text_sha256(content),
        "source_integrity": integrity,
        "source_integrity_sha256": canonical_sha256(integrity),
        "render_proof": dict(render_proof) if render_proof is not None else None,
    }


def _validate_envelope(value: Mapping[str, object]) -> dict[str, object]:
    envelope = dict(value)
    if envelope.get("revision") != "uat-evidence-envelope:v1":
        raise UatRemediationError("UAT_EVIDENCE_ENVELOPE_REVISION_INVALID")
    evidence_id = _safe_identifier(envelope.get("evidence_id"), "UAT_EVIDENCE_ID_INVALID")
    source_document_id = _safe_identifier(
        envelope.get("source_document_id"), "UAT_SOURCE_DOCUMENT_ID_INVALID"
    )
    source_version_sha256 = _sha256(
        envelope.get("source_version_sha256"), "UAT_SOURCE_VERSION_HASH_INVALID"
    )
    content = envelope.get("content")
    locator = envelope.get("locator")
    if not isinstance(content, str) or not isinstance(locator, Mapping):
        raise UatRemediationError("UAT_EVIDENCE_ENVELOPE_SCHEMA_INVALID")
    entity_id = envelope.get("entity_id")
    field_key = envelope.get("field_key")
    if entity_id is not None and not isinstance(entity_id, str):
        raise UatRemediationError("UAT_EVIDENCE_ENTITY_ID_INVALID")
    if field_key is not None and not isinstance(field_key, str):
        raise UatRemediationError("UAT_EVIDENCE_FIELD_KEY_INVALID")
    expected = build_evidence_envelope(
        evidence_id=evidence_id,
        source_document_id=source_document_id,
        source_version_sha256=source_version_sha256,
        content=content,
        locator=locator,
        entity_id=entity_id,
        field_key=field_key,
        rendered_text=None,
        render_proof=None,
    )
    integrity = envelope.get("source_integrity")
    expected_integrity = expected["source_integrity"]
    if not isinstance(integrity, Mapping) or any(
        not isinstance(expected_integrity, Mapping)
        or integrity.get(key) != expected_integrity.get(key)
        for key in ("source_text_sha256", "normalized_text_sha256")
    ):
        raise UatRemediationError("UAT_EVIDENCE_SOURCE_INTEGRITY_INVALID")
    if not isinstance(integrity.get("rendered_text_verified"), bool) or envelope.get(
        "source_integrity_sha256"
    ) != canonical_sha256(dict(integrity)):
        raise UatRemediationError("UAT_EVIDENCE_SOURCE_INTEGRITY_INVALID")
    if integrity["rendered_text_verified"] is True:
        rendered_hash = integrity.get("rendered_text_sha256")
        _sha256(rendered_hash, "UAT_SOURCE_RENDERED_TEXT_HASH_INVALID")
        proof = envelope.get("render_proof")
        if (
            not isinstance(proof, Mapping)
            or proof.get("revision") != "uat-independent-render-proof:v1"
            or proof.get("source_version_sha256") != source_version_sha256
            or proof.get("locator_sha256") != canonical_sha256(dict(locator))
            or proof.get("representation_sha256") != rendered_hash
        ):
            raise UatRemediationError("UAT_RENDER_PROOF_INVALID")
    elif "rendered_text_sha256" in integrity:
        raise UatRemediationError("UAT_EVIDENCE_SOURCE_INTEGRITY_INVALID")
    for key in (
        "content_sha256",
        "locator_sha256",
        "evidence_span_sha256",
        "entity_id",
        "field_key",
    ):
        if envelope.get(key) != expected.get(key):
            raise UatRemediationError("UAT_EVIDENCE_ENVELOPE_HASH_INVALID")
    return envelope


def _claim_from_mapping(value: Mapping[str, object]) -> dict[str, object]:
    required = {
        "assertion_mode",
        "value_text",
        "citation_evidence_id",
        "citation_span_sha256",
        "source_document_id",
        "source_version_sha256",
        "locator_sha256",
        "entity_id",
        "field_key",
    }
    if set(value) != required:
        raise UatRemediationError("UAT_CLAIM_SCHEMA_INVALID")
    if value.get("assertion_mode") != "exact":
        raise UatRemediationError("UAT_CLAIM_ASSERTION_MODE_INVALID")
    claim = dict(value)
    if not isinstance(claim["value_text"], str) or not str(claim["value_text"]).strip():
        raise UatRemediationError("UAT_CLAIM_VALUE_INVALID")
    for key, code in (
        ("citation_evidence_id", "UAT_CLAIM_EVIDENCE_ID_INVALID"),
        ("source_document_id", "UAT_CLAIM_SOURCE_DOCUMENT_ID_INVALID"),
    ):
        _safe_identifier(claim[key], code)
    _optional_identifier(claim["entity_id"], "UAT_CLAIM_ENTITY_ID_INVALID")
    _optional_identifier(claim["field_key"], "UAT_CLAIM_FIELD_KEY_INVALID")
    for key, code in (
        ("citation_span_sha256", "UAT_CLAIM_SPAN_HASH_INVALID"),
        ("source_version_sha256", "UAT_CLAIM_SOURCE_VERSION_HASH_INVALID"),
        ("locator_sha256", "UAT_CLAIM_LOCATOR_HASH_INVALID"),
    ):
        _sha256(claim[key], code)
    return claim


def _render_verified_answer(status: str, claims: Sequence[Mapping[str, object]]) -> str:
    if status != "answered":
        return f"EVIDENCE_STATUS:{status}"
    return "\n".join(f"{claim['field_key']}: {claim['value_text']}" for claim in claims)


def validate_claim_response(
    response: Mapping[str, object],
    evidence: Sequence[Mapping[str, object]],
    *,
    allow_cross_document: bool = False,
) -> dict[str, object]:
    """Validate a structured response before any user-facing answer is rendered."""

    if set(response) != {"status", "claims"}:
        raise UatRemediationError("UAT_CLAIM_RESPONSE_SCHEMA_INVALID")
    status = response.get("status")
    raw_claims = response.get("claims")
    if (
        status not in _ALLOWED_STATUSES
        or not isinstance(raw_claims, Sequence)
        or isinstance(raw_claims, (str, bytes))
    ):
        raise UatRemediationError("UAT_CLAIM_RESPONSE_SCHEMA_INVALID")
    envelopes = [_validate_envelope(item) for item in evidence]
    envelope_by_id = {str(item["evidence_id"]): item for item in envelopes}
    if len(envelope_by_id) != len(envelopes):
        raise UatRemediationError("UAT_EVIDENCE_ID_DUPLICATE")
    claims = [
        _claim_from_mapping(item) if isinstance(item, Mapping) else _raise_claim_schema()
        for item in raw_claims
    ]
    if status == "answered":
        if not claims:
            raise UatRemediationError("UAT_ANSWERED_CLAIMS_MISSING")
    elif claims:
        raise UatRemediationError("UAT_NONANSWERED_CLAIMS_FORBIDDEN")
    source_ids: set[str] = set()
    citation_ids: list[str] = []
    for claim in claims:
        evidence_id = str(claim["citation_evidence_id"])
        envelope = envelope_by_id.get(evidence_id)
        if envelope is None:
            raise UatRemediationError("UAT_CLAIM_EVIDENCE_UNKNOWN")
        if any(
            claim[claim_key] != envelope[envelope_key]
            for claim_key, envelope_key in (
                ("source_document_id", "source_document_id"),
                ("source_version_sha256", "source_version_sha256"),
                ("locator_sha256", "locator_sha256"),
                ("entity_id", "entity_id"),
                ("field_key", "field_key"),
                ("citation_span_sha256", "evidence_span_sha256"),
            )
        ):
            raise UatRemediationError("UAT_CLAIM_PROVENANCE_MISMATCH")
        value_text = str(claim["value_text"])
        content = str(envelope["content"])
        if value_text not in content:
            raise UatRemediationError("UAT_CLAIM_VALUE_NOT_IN_EVIDENCE")
        source_ids.add(str(envelope["source_document_id"]))
        citation_ids.append(evidence_id)
    if len(set(citation_ids)) != len(citation_ids):
        raise UatRemediationError("UAT_CLAIM_CITATION_DUPLICATE")
    if len(source_ids) > 1 and not allow_cross_document:
        raise UatRemediationError("UAT_CLAIM_CROSS_DOCUMENT_FORBIDDEN")
    locator_grounded = bool(claims) and all(
        claim["locator_sha256"]
        == envelope_by_id[str(claim["citation_evidence_id"])]["locator_sha256"]
        for claim in claims
    )
    return {
        "revision": "uat-claim-validation:v1",
        "status": status,
        "claims": claims,
        "citation_ids": citation_ids,
        "answer": _render_verified_answer(str(status), claims),
        "locator_grounded": locator_grounded,
        "claim_snapshot_sha256": canonical_sha256(claims),
    }


def build_claim_contract_request(
    question: str,
    evidence: Sequence[Mapping[str, object]],
    *,
    allow_cross_document: bool = False,
) -> dict[str, object]:
    """Build the versioned model contract without weakening evidence provenance."""

    if not isinstance(question, str) or not question.strip():
        raise UatRemediationError("UAT_CLAIM_QUESTION_INVALID")
    envelopes = [_validate_envelope(item) for item in evidence]
    return {
        "revision": "uat-claim-contract:v1",
        "question": question,
        "allow_cross_document": allow_cross_document,
        "evidence": [
            {
                "evidence_id": item["evidence_id"],
                "source_document_id": item["source_document_id"],
                "source_version_sha256": item["source_version_sha256"],
                "content": item["content"],
                "content_sha256": item["content_sha256"],
                "locator": item["locator"],
                "locator_sha256": item["locator_sha256"],
                "evidence_span_sha256": item["evidence_span_sha256"],
                "entity_id": item["entity_id"],
                "field_key": item["field_key"],
                "source_integrity": item["source_integrity"],
                "source_integrity_sha256": item["source_integrity_sha256"],
                "render_proof": item["render_proof"],
            }
            for item in envelopes
        ],
        "required_output": {
            "status": "answered|insufficient_evidence|needs_clarification|conflicting_evidence",
            "claims": [
                {
                    "assertion_mode": "exact",
                    "value_text": "exact substring from evidence",
                    "citation_evidence_id": "evidence ID",
                    "citation_span_sha256": "evidence span hash",
                    "source_document_id": "source document ID",
                    "source_version_sha256": "source version hash",
                    "locator_sha256": "locator hash",
                    "entity_id": "entity ID or null",
                    "field_key": "field key or null",
                }
            ],
        },
    }


def _raise_claim_schema() -> Any:
    raise UatRemediationError("UAT_CLAIM_SCHEMA_INVALID")


def build_audit_manifest(
    *,
    test_case_id: str,
    question_sha256: str,
    bundle_sha256: str,
    evidence: Sequence[Mapping[str, object]],
    validated_response: Mapping[str, object],
    allow_cross_document: bool = False,
) -> dict[str, object]:
    """Build a content-free, replayable export for a validated UAT result."""

    case_id = _safe_identifier(test_case_id, "UAT_TEST_CASE_ID_INVALID")
    _sha256(question_sha256, "UAT_AUDIT_QUESTION_HASH_INVALID")
    _sha256(bundle_sha256, "UAT_AUDIT_BUNDLE_HASH_INVALID")
    envelopes = [_validate_envelope(item) for item in evidence]
    response = validate_claim_response(
        validated_response, envelopes, allow_cross_document=allow_cross_document
    )
    evidence_records = []
    for item in sorted(envelopes, key=lambda item: str(item["evidence_id"])):
        integrity = item["source_integrity"]
        if not isinstance(integrity, Mapping):
            raise UatRemediationError("UAT_EVIDENCE_SOURCE_INTEGRITY_INVALID")
        evidence_records.append(
            {
                "evidence_id": item["evidence_id"],
                "source_document_id": item["source_document_id"],
                "source_version_sha256": item["source_version_sha256"],
                "content_sha256": item["content_sha256"],
                "locator_sha256": item["locator_sha256"],
                "evidence_span_sha256": item["evidence_span_sha256"],
                "entity_id": item["entity_id"],
                "field_key": item["field_key"],
                "source_integrity_sha256": item["source_integrity_sha256"],
                "rendered_text_verified": integrity["rendered_text_verified"],
                "render_proof_sha256": canonical_sha256(item["render_proof"]),
            }
        )
    return {
        "revision": "uat-audit-manifest:v1",
        "test_case_id": case_id,
        "question_sha256": question_sha256,
        "bundle_sha256": bundle_sha256,
        "evidence": evidence_records,
        "evidence_snapshot_sha256": canonical_sha256(evidence_records),
        "status": response["status"],
        "citation_ids": response["citation_ids"],
        "claim_snapshot_sha256": response["claim_snapshot_sha256"],
        "allow_cross_document": allow_cross_document,
        "locator_grounded": response["locator_grounded"],
        "answer_sha256": text_sha256(str(response["answer"])),
        "content_output": False,
    }


def validate_audit_coverage(
    manifests: Sequence[Mapping[str, object]], expected_case_ids: Sequence[str]
) -> dict[str, object]:
    """Require one immutable audit manifest for every expected test case."""

    expected = [_safe_identifier(value, "UAT_TEST_CASE_ID_INVALID") for value in expected_case_ids]
    if len(set(expected)) != len(expected):
        raise UatRemediationError("UAT_AUDIT_EXPECTED_CASE_ID_DUPLICATE")
    actual: list[str] = []
    for manifest in manifests:
        if manifest.get("revision") != "uat-audit-manifest:v1":
            raise UatRemediationError("UAT_AUDIT_MANIFEST_REVISION_INVALID")
        case_id = _safe_identifier(manifest.get("test_case_id"), "UAT_TEST_CASE_ID_INVALID")
        required = {
            "revision",
            "test_case_id",
            "question_sha256",
            "bundle_sha256",
            "evidence",
            "evidence_snapshot_sha256",
            "status",
            "citation_ids",
            "claim_snapshot_sha256",
            "allow_cross_document",
            "locator_grounded",
            "answer_sha256",
            "content_output",
        }
        if set(manifest) != required or manifest.get("content_output") is not False:
            raise UatRemediationError("UAT_AUDIT_MANIFEST_SCHEMA_INVALID")
        actual.append(case_id)
    if len(set(actual)) != len(actual) or set(actual) != set(expected):
        raise UatRemediationError("UAT_AUDIT_COVERAGE_MISMATCH")
    return {
        "revision": "uat-audit-coverage:v1",
        "expected_case_count": len(expected),
        "manifest_count": len(actual),
        "coverage_complete": True,
        "case_snapshot_sha256": canonical_sha256(sorted(actual)),
        "content_output": False,
    }


def build_audit_coverage_manifest(
    manifests: Sequence[Mapping[str, object]],
    expected_case_ids: Sequence[str],
    *,
    input_snapshot_sha256: str,
    audit_refs: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """Bind full case coverage to immutable per-case audit references."""

    _sha256(input_snapshot_sha256, "UAT_AUDIT_INPUT_SNAPSHOT_HASH_INVALID")
    coverage = validate_audit_coverage(manifests, expected_case_ids)
    records = []
    for case_id in sorted(str(value) for value in expected_case_ids):
        reference = audit_refs.get(case_id)
        if (
            not isinstance(reference, Mapping)
            or reference.get("test_case_id") != case_id
            or not isinstance(reference.get("audit_ref"), str)
        ):
            raise UatRemediationError("UAT_AUDIT_COVERAGE_REFERENCE_INVALID")
        _sha256(reference.get("audit_sha256"), "UAT_AUDIT_COVERAGE_HASH_INVALID")
        records.append(
            {
                "test_case_id": case_id,
                "audit_ref": reference["audit_ref"],
                "audit_sha256": reference["audit_sha256"],
            }
        )
    return {
        "revision": "uat-audit-coverage-manifest:v1",
        "input_snapshot_sha256": input_snapshot_sha256,
        "coverage": coverage,
        "audit_records": records,
        "audit_records_sha256": canonical_sha256(records),
        "content_output": False,
    }


def validate_audit_coverage_manifest(
    manifest: Mapping[str, object],
    manifests: Sequence[Mapping[str, object]],
    expected_case_ids: Sequence[str],
    *,
    input_snapshot_sha256: str,
    audit_refs: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """Reject missing or mismatched coverage before a completed run is accepted."""

    expected = build_audit_coverage_manifest(
        manifests,
        expected_case_ids,
        input_snapshot_sha256=input_snapshot_sha256,
        audit_refs=audit_refs,
    )
    if dict(manifest) != expected:
        raise UatRemediationError("UAT_AUDIT_COVERAGE_MANIFEST_MISMATCH")
    return expected
