from __future__ import annotations

import json
from pathlib import Path

import pytest
from ragkb.application.uat_future_claim_runner import (
    FutureErrorCaseRetestRunner,
    FutureUatClaimRunner,
    require_future_case_egress,
)
from ragkb.contracts.provider_execution import ExecutionApprovalRequired, ProviderExecutionError
from ragkb.evaluation.uat_generic_remediation import (
    UatRemediationError,
    canonical_sha256,
    text_sha256,
)
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore


class _ClaimTransport:
    real_network = False

    def __init__(self, *, invalid: bool = False, multi_document: bool = False) -> None:
        self.invalid = invalid
        self.multi_document = multi_document
        self.contracts: list[dict[str, object]] = []

    def generate_claims(self, contract, idempotency_key, timeout_seconds):
        del idempotency_key, timeout_seconds
        captured = dict(contract)
        self.contracts.append(captured)
        evidence_rows = contract["evidence"] if self.multi_document else contract["evidence"][:1]
        claims = [
            {
                "assertion_mode": "exact",
                "value_text": "invalid" if self.invalid else evidence["content"],
                "citation_evidence_id": evidence["evidence_id"],
                "citation_span_sha256": evidence["evidence_span_sha256"],
                "source_document_id": evidence["source_document_id"],
                "source_version_sha256": evidence["source_version_sha256"],
                "locator_sha256": evidence["locator_sha256"],
                "entity_id": evidence["entity_id"],
                "field_key": evidence["field_key"],
            }
            for evidence in evidence_rows
        ]
        return {"status": "answered", "claims": claims}


def _case(
    seed: int,
    *,
    optional_identity: bool = False,
    render_matches: bool = True,
    allow_cross_document: bool = False,
):
    content = f"field-{seed}=value-{seed}"
    locator = {"page": seed + 1}
    return {
        "test_case_id": f"case-{seed}",
        "question": f"request-{seed}",
        "allow_cross_document": allow_cross_document,
        "source_classification": "confidential",
        "evidence": [
            {
                "evidence_id": f"evidence-{seed}",
                "source_document_id": f"document-{seed}",
                "source_version_sha256": text_sha256(f"version-{seed}"),
                "content": content,
                "rendered_text": content if render_matches else f"other-{seed}",
                "render_proof": {
                    "revision": "uat-independent-render-proof:v1",
                    "source_version_sha256": text_sha256(f"version-{seed}"),
                    "locator_sha256": canonical_sha256(locator),
                    "representation_sha256": text_sha256(content),
                },
                "locator": locator,
                "entity_id": None if optional_identity else f"entity-{seed}",
                "field_key": None if optional_identity else f"field-{seed}",
            }
        ],
    }


def _additional_evidence(seed: int) -> dict[str, object]:
    content = f"field-extra-{seed}=value-extra-{seed}"
    locator = {"page": seed + 2}
    return {
        "evidence_id": f"evidence-extra-{seed}",
        "source_document_id": f"document-extra-{seed}",
        "source_version_sha256": text_sha256(f"version-extra-{seed}"),
        "content": content,
        "rendered_text": content,
        "render_proof": {
            "revision": "uat-independent-render-proof:v1",
            "source_version_sha256": text_sha256(f"version-extra-{seed}"),
            "locator_sha256": canonical_sha256(locator),
            "representation_sha256": text_sha256(content),
        },
        "locator": locator,
        "entity_id": f"entity-extra-{seed}",
        "field_key": f"field-extra-{seed}",
    }


def test_future_runner_wires_envelope_contract_validator_and_audit(tmp_path: Path) -> None:
    transport = _ClaimTransport()
    checkpoints = JsonCheckpointStore(tmp_path / "future-claims.json")
    artifacts = LocalUatArtifactStore(tmp_path / "artifacts")
    cases = [_case(1), _case(2, optional_identity=True)]
    runner = FutureUatClaimRunner(
        transport, checkpoints, artifacts, external_call_approved=False, max_requests=2
    )
    result = runner.run(cases)
    assert result["request_count"] == result["completed_count"] == 2
    assert result["historical_artifacts_mutated"] is False
    assert len(transport.contracts) == 2
    for contract in transport.contracts:
        evidence = contract["evidence"]
        assert isinstance(evidence, list)
        assert evidence[0]["source_integrity"]["rendered_text_verified"] is True
        assert "source_document_id" in evidence[0]
        assert "locator_sha256" in evidence[0]
    assert len(list(artifacts.claim_audit_root.glob("case-*.json"))) == 2
    assert len(list(artifacts.claim_result_root.glob("*.json"))) == 2
    assert artifacts.claim_coverage_path.is_file()
    audit = json.loads((artifacts.claim_audit_root / "case-1.json").read_text(encoding="utf-8"))
    assert audit["evidence"][0]["rendered_text_verified"] is True
    assert "source_integrity_sha256" in audit["evidence"][0]
    checkpoint_text = checkpoints.path.read_text(encoding="utf-8")
    assert '"question"' not in checkpoint_text
    assert '"content"' not in checkpoint_text
    runner.run(cases)
    assert len(transport.contracts) == 2


def test_future_runner_allows_explicit_multi_document_claims_and_persists_coverage(
    tmp_path: Path,
) -> None:
    case = _case(6, allow_cross_document=True)
    case["evidence"].append(_additional_evidence(6))
    transport = _ClaimTransport(multi_document=True)
    artifacts = LocalUatArtifactStore(tmp_path / "artifacts")
    runner = FutureUatClaimRunner(
        transport,
        JsonCheckpointStore(tmp_path / "claims.json"),
        artifacts,
        external_call_approved=False,
        max_requests=1,
    )
    result = runner.run([case])
    assert result["coverage_complete"] is True
    audit = json.loads((artifacts.claim_audit_root / "case-6.json").read_text(encoding="utf-8"))
    assert audit["allow_cross_document"] is True
    coverage = artifacts.read_claim_coverage_manifest()
    assert coverage is not None
    assert coverage["coverage"]["coverage_complete"] is True
    runner.run([case])
    assert len(transport.contracts) == 1


def test_future_runner_rejects_default_multi_document_claims_and_coverage_mismatch(
    tmp_path: Path,
) -> None:
    forbidden = _case(7)
    forbidden["evidence"].append(_additional_evidence(7))
    transport = _ClaimTransport(multi_document=True)
    runner = FutureUatClaimRunner(
        transport,
        JsonCheckpointStore(tmp_path / "forbidden.json"),
        LocalUatArtifactStore(tmp_path / "forbidden-artifacts"),
        external_call_approved=False,
        max_requests=1,
    )
    with pytest.raises(ProviderExecutionError, match="UAT_FUTURE_CLAIM_FAILURE"):
        runner.run([forbidden])
    assert len(transport.contracts) == 1

    complete = _case(8)
    complete_transport = _ClaimTransport()
    complete_artifacts = LocalUatArtifactStore(tmp_path / "complete-artifacts")
    complete_runner = FutureUatClaimRunner(
        complete_transport,
        JsonCheckpointStore(tmp_path / "complete.json"),
        complete_artifacts,
        external_call_approved=False,
        max_requests=1,
    )
    complete_runner.run([complete])
    complete_artifacts.claim_coverage_path.write_text('{"revision":"invalid"}\n', encoding="utf-8")
    with pytest.raises(ProviderExecutionError, match="COVERAGE"):
        complete_runner.run([complete])
    assert len(complete_transport.contracts) == 1


def test_error_retest_runner_uses_an_independent_namespace_and_artifact_revision(
    tmp_path: Path,
) -> None:
    transport = _ClaimTransport()
    checkpoints = JsonCheckpointStore(tmp_path / "retest.json")
    artifacts = LocalUatArtifactStore(tmp_path / "artifacts", claim_revision="error-retest-v1")
    result = FutureErrorCaseRetestRunner(
        transport, checkpoints, artifacts, external_call_approved=False, max_requests=1
    ).run([_case(9)])
    assert result["revision"] == "uat-future-error-retest-runner:v1"
    checkpoint = json.loads(checkpoints.path.read_text(encoding="utf-8"))
    assert "uat_future_error_retest_v1" in checkpoint
    assert artifacts.claim_result_root.parts[-1] == "error-retest-v1"
    assert artifacts.claim_audit_root.parts[-1] == "error-retest-v1"


def test_future_case_egress_requires_region_and_allowed_classification() -> None:
    case = _case(10)
    require_future_case_egress(
        [case],
        outbound_ai_allowed=True,
        allowed_classifications=["confidential"],
        approved_processing_regions=["approved-region"],
    )
    with pytest.raises(ExecutionApprovalRequired, match="REGION_NOT_APPROVED"):
        require_future_case_egress(
            [case],
            outbound_ai_allowed=True,
            allowed_classifications=["confidential"],
            approved_processing_regions=[],
        )
    denied = {**case, "source_classification": "restricted"}
    with pytest.raises(ExecutionApprovalRequired, match="EGRESS_POLICY_DENIED"):
        require_future_case_egress(
            [denied],
            outbound_ai_allowed=True,
            allowed_classifications=["confidential", "restricted"],
            approved_processing_regions=["approved-region"],
        )


def test_future_runner_rejects_source_and_claim_mutations_before_next_request(
    tmp_path: Path,
) -> None:
    source_transport = _ClaimTransport()
    source_runner = FutureUatClaimRunner(
        source_transport,
        JsonCheckpointStore(tmp_path / "source.json"),
        LocalUatArtifactStore(tmp_path / "source-artifacts"),
        external_call_approved=False,
        max_requests=1,
    )
    with pytest.raises(UatRemediationError, match="UAT_SOURCE_RENDERED_TEXT_MISMATCH"):
        source_runner.run([_case(3, render_matches=False)])
    assert source_transport.contracts == []

    claim_transport = _ClaimTransport(invalid=True)
    claim_runner = FutureUatClaimRunner(
        claim_transport,
        JsonCheckpointStore(tmp_path / "claim.json"),
        LocalUatArtifactStore(tmp_path / "claim-artifacts"),
        external_call_approved=False,
        max_requests=2,
    )
    with pytest.raises(ProviderExecutionError, match="UAT_FUTURE_CLAIM_FAILURE"):
        claim_runner.run([_case(4), _case(5)])
    assert len(claim_transport.contracts) == 1
    failed = json.loads((tmp_path / "claim.json").read_text(encoding="utf-8"))[
        "uat_future_claim_v1"
    ]
    records = [item for key, item in failed.items() if key != "_manifest"]
    assert records[0]["state"] == "FAILED"
    assert records[0]["automatic_retries"] == 0
