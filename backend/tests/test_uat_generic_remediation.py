from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
from ragkb.evaluation.uat_generic_remediation import (
    UatRemediationError,
    build_audit_manifest,
    build_claim_contract_request,
    build_evidence_envelope,
    text_sha256,
    validate_audit_coverage,
    validate_claim_response,
    validate_source_integrity,
)
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore


def _envelope(seed: int, *, document_seed: int | None = None) -> dict[str, object]:
    document = seed if document_seed is None else document_seed
    content = f"field-{seed}=value-{seed}"
    return build_evidence_envelope(
        evidence_id=f"evidence-{seed}",
        source_document_id=f"document-{document}",
        source_version_sha256=text_sha256(f"version-{document}"),
        content=content,
        locator={"page": seed + 1},
        entity_id=f"entity-{seed}",
        field_key=f"field-{seed}",
    )


def _claim(envelope: dict[str, object]) -> dict[str, object]:
    return {
        "assertion_mode": "exact",
        "value_text": str(envelope["content"]),
        "citation_evidence_id": envelope["evidence_id"],
        "citation_span_sha256": envelope["evidence_span_sha256"],
        "source_document_id": envelope["source_document_id"],
        "source_version_sha256": envelope["source_version_sha256"],
        "locator_sha256": envelope["locator_sha256"],
        "entity_id": envelope["entity_id"],
        "field_key": envelope["field_key"],
    }


def test_generic_claim_contract_preserves_all_provenance_dimensions() -> None:
    for seed in range(1, 10):
        envelope = _envelope(seed)
        response = {"status": "answered", "claims": [_claim(envelope)]}
        validated = validate_claim_response(response, [envelope])
        assert validated["citation_ids"] == [envelope["evidence_id"]]
        assert validated["locator_grounded"] is True
        assert str(validated["answer"]).endswith(str(envelope["content"]))
        contract = build_claim_contract_request("generic request", [envelope])
        prompt_evidence = contract["evidence"]
        assert isinstance(prompt_evidence, list)
        assert prompt_evidence[0]["source_document_id"] == envelope["source_document_id"]
        assert prompt_evidence[0]["locator_sha256"] == envelope["locator_sha256"]


@pytest.mark.parametrize(
    ("path", "code"),
    [
        (("claims", 0, "value_text"), "UAT_CLAIM_VALUE_NOT_IN_EVIDENCE"),
        (("claims", 0, "entity_id"), "UAT_CLAIM_PROVENANCE_MISMATCH"),
        (("claims", 0, "field_key"), "UAT_CLAIM_PROVENANCE_MISMATCH"),
        (("claims", 0, "source_document_id"), "UAT_CLAIM_PROVENANCE_MISMATCH"),
        (("claims", 0, "source_version_sha256"), "UAT_CLAIM_PROVENANCE_MISMATCH"),
        (("claims", 0, "locator_sha256"), "UAT_CLAIM_PROVENANCE_MISMATCH"),
        (("claims", 0, "citation_span_sha256"), "UAT_CLAIM_PROVENANCE_MISMATCH"),
    ],
)
def test_claim_mutations_fail_before_render(path: tuple[object, ...], code: str) -> None:
    envelope = _envelope(11)
    response = {"status": "answered", "claims": [_claim(envelope)]}
    mutated = copy.deepcopy(response)
    target = mutated
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    field = path[-1]
    target[field] = text_sha256("mutation") if str(field).endswith("sha256") else "mutation"  # type: ignore[index]
    with pytest.raises(UatRemediationError, match=code):
        validate_claim_response(mutated, [envelope])


def test_cross_document_and_status_body_mutations_are_rejected() -> None:
    first = _envelope(21, document_seed=1)
    second = _envelope(22, document_seed=2)
    response = {"status": "answered", "claims": [_claim(first), _claim(second)]}
    with pytest.raises(UatRemediationError, match="CROSS_DOCUMENT_FORBIDDEN"):
        validate_claim_response(response, [first, second])
    assert (
        validate_claim_response(response, [first, second], allow_cross_document=True)[
            "locator_grounded"
        ]
        is True
    )
    for status in ("insufficient_evidence", "needs_clarification", "conflicting_evidence"):
        with pytest.raises(UatRemediationError, match="NONANSWERED_CLAIMS_FORBIDDEN"):
            validate_claim_response({"status": status, "claims": [_claim(first)]}, [first])
        validated = validate_claim_response({"status": status, "claims": []}, [first])
        assert validated["answer"] == f"EVIDENCE_STATUS:{status}"
        assert validated["locator_grounded"] is False


def test_source_integrity_gate_rejects_corruption_and_representation_mismatch() -> None:
    assert (
        validate_source_integrity("alpha beta", rendered_text="alpha beta")[
            "rendered_text_verified"
        ]
        is True
    )
    with pytest.raises(UatRemediationError, match="CONTROL_OR_REPLACEMENT"):
        validate_source_integrity("alpha\x00beta")
    with pytest.raises(UatRemediationError, match="RENDERED_TEXT_MISMATCH"):
        validate_source_integrity("alpha beta", rendered_text="alpha gamma")


def test_content_free_audit_export_is_immutable_and_coverage_complete(tmp_path: Path) -> None:
    envelope = _envelope(31)
    response = {"status": "answered", "claims": [_claim(envelope)]}
    manifest = build_audit_manifest(
        test_case_id="case-31",
        question_sha256=text_sha256("request-31"),
        bundle_sha256=text_sha256("bundle-31"),
        evidence=[envelope],
        validated_response=response,
    )
    assert '"content"' not in str(manifest)
    assert '"answer"' not in str(manifest)
    coverage = validate_audit_coverage([manifest], ["case-31"])
    assert coverage["coverage_complete"] is True
    with pytest.raises(UatRemediationError, match="COVERAGE_MISMATCH"):
        validate_audit_coverage([manifest], ["case-32"])
    store = LocalUatArtifactStore(tmp_path / "artifacts")
    stored = store.persist_claim_audit_manifest("case-31", manifest)
    assert store.persist_claim_audit_manifest("case-31", manifest) == stored
    persisted = (store.claim_audit_root / "case-31.json").read_text(encoding="utf-8")
    assert '"content"' not in persisted
    assert '"answer"' not in persisted
    changed = {**manifest, "status": "needs_clarification"}
    with pytest.raises(ValueError, match="IMMUTABLE_MISMATCH"):
        store.persist_claim_audit_manifest("case-31", changed)


def test_new_remediation_sources_have_no_historical_case_identifier_literals() -> None:
    root = Path(__file__).resolve().parents[2]
    sources = [
        root / "backend/src/ragkb/evaluation/uat_generic_remediation.py",
        root / "backend/tests/test_uat_generic_remediation.py",
    ]
    assert all(
        re.search(r"\b[0-9a-f]{20}\b", path.read_text(encoding="utf-8")) is None for path in sources
    )
