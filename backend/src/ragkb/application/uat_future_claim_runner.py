"""Future-only execution path for the versioned structured-claim UAT contract."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from ragkb.application.provider_runners import require_configured_provider_egress
from ragkb.contracts.provider_execution import (
    CheckpointStorePort,
    ExecutionApprovalRequired,
    ProviderExecutionError,
    UatClaimArtifactStorePort,
    UatClaimTransportPort,
)
from ragkb.evaluation.uat_generic_remediation import (
    UatRemediationError,
    build_audit_coverage_manifest,
    build_audit_manifest,
    build_claim_contract_request,
    build_evidence_envelope,
    canonical_sha256,
    text_sha256,
    validate_audit_coverage_manifest,
    validate_claim_response,
)

_SAFE_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_RAW_EVIDENCE_REQUIRED = {
    "evidence_id",
    "source_document_id",
    "source_version_sha256",
    "content",
    "locator",
}
_RAW_EVIDENCE_OPTIONAL = {"entity_id", "field_key", "rendered_text", "render_proof"}


class _PreparedCase(TypedDict):
    binding: dict[str, object]
    envelopes: list[dict[str, object]]
    contract: dict[str, object]


class FutureUatClaimRunner:
    """Run only newly submitted cases; existing UAT checkpoints are never consulted."""

    revision = "uat-future-claim-runner:v1"
    namespace = "uat_future_claim_v1"
    artifact_revision = "v1"

    def __init__(
        self,
        transport: UatClaimTransportPort,
        checkpoints: CheckpointStorePort,
        artifacts: UatClaimArtifactStorePort,
        *,
        external_call_approved: bool,
        max_requests: int,
        timeout_seconds: float = 120,
    ) -> None:
        self.transport = transport
        self.checkpoints = checkpoints
        self.artifacts = artifacts
        self.external_call_approved = external_call_approved
        self.max_requests = max_requests
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _case_id(value: object) -> str:
        if not isinstance(value, str) or not _SAFE_CASE_ID.fullmatch(value):
            raise UatRemediationError("UAT_FUTURE_CASE_ID_INVALID")
        return value

    @staticmethod
    def _raw_envelope(value: Mapping[str, object]) -> dict[str, object]:
        if not _RAW_EVIDENCE_REQUIRED.issubset(value) or not set(value).issubset(
            _RAW_EVIDENCE_REQUIRED | _RAW_EVIDENCE_OPTIONAL
        ):
            raise UatRemediationError("UAT_FUTURE_EVIDENCE_SCHEMA_INVALID")
        rendered = value.get("rendered_text")
        proof = value.get("render_proof")
        if rendered is not None and not isinstance(rendered, str):
            raise UatRemediationError("UAT_FUTURE_RENDERED_TEXT_INVALID")
        if proof is not None and not isinstance(proof, Mapping):
            raise UatRemediationError("UAT_FUTURE_RENDER_PROOF_INVALID")
        entity = value.get("entity_id")
        field = value.get("field_key")
        if entity is not None and not isinstance(entity, str):
            raise UatRemediationError("UAT_FUTURE_ENTITY_ID_INVALID")
        if field is not None and not isinstance(field, str):
            raise UatRemediationError("UAT_FUTURE_FIELD_KEY_INVALID")
        locator = value["locator"]
        if not isinstance(locator, Mapping):
            raise UatRemediationError("UAT_FUTURE_LOCATOR_INVALID")
        for key in ("evidence_id", "source_document_id", "source_version_sha256", "content"):
            if not isinstance(value[key], str):
                raise UatRemediationError("UAT_FUTURE_EVIDENCE_SCHEMA_INVALID")
        return build_evidence_envelope(
            evidence_id=str(value["evidence_id"]),
            source_document_id=str(value["source_document_id"]),
            source_version_sha256=str(value["source_version_sha256"]),
            content=str(value["content"]),
            locator=locator,
            entity_id=entity,
            field_key=field,
            rendered_text=rendered,
            render_proof=proof,
        )

    def _prepared_case(self, value: Mapping[str, object]) -> _PreparedCase:
        required = {
            "test_case_id",
            "question",
            "evidence",
            "allow_cross_document",
            "source_classification",
        }
        if set(value) != required:
            raise UatRemediationError("UAT_FUTURE_CASE_SCHEMA_INVALID")
        case_id = self._case_id(value["test_case_id"])
        question = value["question"]
        evidence = value["evidence"]
        allow_cross_document = value["allow_cross_document"]
        source_classification = value["source_classification"]
        if (
            not isinstance(question, str)
            or not isinstance(evidence, Sequence)
            or isinstance(evidence, (str, bytes))
            or not isinstance(allow_cross_document, bool)
            or not isinstance(source_classification, str)
            or not source_classification
        ):
            raise UatRemediationError("UAT_FUTURE_CASE_SCHEMA_INVALID")
        envelopes = [
            self._raw_envelope(item)
            if isinstance(item, Mapping)
            else _raise_future_evidence_schema()
            for item in evidence
        ]
        contract = build_claim_contract_request(
            question, envelopes, allow_cross_document=allow_cross_document
        )
        evidence_binding = []
        for item in envelopes:
            integrity = item["source_integrity"]
            if not isinstance(integrity, Mapping):
                raise UatRemediationError("UAT_FUTURE_SOURCE_INTEGRITY_INVALID")
            evidence_binding.append(
                {
                    "evidence_id": item["evidence_id"],
                    "source_document_id": item["source_document_id"],
                    "source_version_sha256": item["source_version_sha256"],
                    "content_sha256": item["content_sha256"],
                    "locator_sha256": item["locator_sha256"],
                    "evidence_span_sha256": item["evidence_span_sha256"],
                    "source_integrity_sha256": item["source_integrity_sha256"],
                    "rendered_text_verified": integrity["rendered_text_verified"],
                    "entity_id": item["entity_id"],
                    "field_key": item["field_key"],
                }
            )
        binding = {
            "test_case_id": case_id,
            "question_sha256": text_sha256(question),
            "bundle_sha256": canonical_sha256(evidence_binding),
            "evidence_snapshot_sha256": canonical_sha256(evidence_binding),
            "allow_cross_document": allow_cross_document,
            "source_classification": source_classification,
        }
        return {"binding": binding, "envelopes": envelopes, "contract": contract}

    def _ensure_coverage(
        self,
        prepared: Sequence[_PreparedCase],
        manifest_parameters: Mapping[str, object],
    ) -> dict[str, object]:
        bindings = [dict(item["binding"]) for item in prepared]
        case_ids = [str(binding["test_case_id"]) for binding in bindings]
        audits: list[Mapping[str, object]] = []
        audit_refs: dict[str, Mapping[str, object]] = {}
        checkpoints: list[dict[str, object]] = []
        for case_id in case_ids:
            checkpoint = self.checkpoints.get(self.namespace, case_id)
            if checkpoint is None or checkpoint.get("state") != "COMPLETED":
                raise ProviderExecutionError("UAT_FUTURE_CLAIM_COVERAGE_INCOMPLETE")
            audit_ref = checkpoint.get("audit_ref")
            audit_sha256 = checkpoint.get("audit_sha256")
            if not isinstance(audit_ref, str) or not isinstance(audit_sha256, str):
                raise ProviderExecutionError("UAT_FUTURE_CLAIM_COVERAGE_AUDIT_REF_MISSING")
            audit = self.artifacts.read_claim_audit_manifest(case_id)
            audits.append(audit)
            audit_refs[case_id] = {
                "test_case_id": case_id,
                "audit_ref": audit_ref,
                "audit_sha256": audit_sha256,
            }
            checkpoints.append(checkpoint)
        try:
            coverage = build_audit_coverage_manifest(
                audits,
                case_ids,
                input_snapshot_sha256=str(manifest_parameters["input_snapshot_sha256"]),
                audit_refs=audit_refs,
            )
            existing = self.artifacts.read_claim_coverage_manifest()
            if existing is None:
                stored = self.artifacts.persist_claim_coverage_manifest(coverage)
            else:
                validate_audit_coverage_manifest(
                    existing,
                    audits,
                    case_ids,
                    input_snapshot_sha256=str(manifest_parameters["input_snapshot_sha256"]),
                    audit_refs=audit_refs,
                )
                stored = {
                    "coverage_ref": (f"uat-claim-audits/{self.artifact_revision}/coverage.json"),
                    "coverage_sha256": canonical_sha256(existing),
                }
        except (UatRemediationError, ValueError) as error:
            raise ProviderExecutionError(
                "UAT_FUTURE_CLAIM_COVERAGE_INVALID", outcome_unknown=False
            ) from error
        coverage_sha256 = canonical_sha256(coverage)
        for checkpoint in checkpoints:
            checkpoint.update(
                coverage_ref=stored["coverage_ref"],
                coverage_sha256=coverage_sha256,
                coverage_complete=True,
            )
            self.checkpoints.save(self.namespace, str(checkpoint["test_case_id"]), checkpoint)
        return {
            "coverage_ref": stored["coverage_ref"],
            "coverage_sha256": coverage_sha256,
            "coverage_complete": True,
        }

    def run(self, cases: Sequence[Mapping[str, object]]) -> dict[str, object]:
        if self.transport.real_network and not self.external_call_approved:
            raise ExecutionApprovalRequired("UAT_FUTURE_CLAIM_EXECUTION_APPROVAL_REQUIRED")
        if not cases or self.max_requests < len(cases):
            raise ProviderExecutionError("UAT_FUTURE_CLAIM_REQUEST_BUDGET_INVALID")
        prepared = [self._prepared_case(case) for case in cases]
        bindings = [dict(item["binding"]) for item in prepared]
        case_ids = [str(binding["test_case_id"]) for binding in bindings]
        if len(set(case_ids)) != len(case_ids):
            raise ProviderExecutionError("UAT_FUTURE_CLAIM_CASE_ID_DUPLICATE")
        manifest_parameters = {
            "revision": self.revision,
            "case_count": len(prepared),
            "max_requests": self.max_requests,
            "automatic_retries": 0,
            "input_snapshot_sha256": canonical_sha256(bindings),
            "historical_artifacts_mutated": False,
        }
        manifest = self.checkpoints.get(self.namespace, "_manifest")
        if manifest is None:
            manifest = {**manifest_parameters, "request_count": 0}
            self.checkpoints.save(self.namespace, "_manifest", manifest)
        elif any(manifest.get(key) != value for key, value in manifest_parameters.items()):
            raise ProviderExecutionError("UAT_FUTURE_CLAIM_SNAPSHOT_OR_PARAMETERS_MISMATCH")
        completed = 0
        for item in prepared:
            binding = dict(item["binding"])
            case_id = str(binding["test_case_id"])
            checkpoint = self.checkpoints.get(self.namespace, case_id)
            if checkpoint is not None:
                if any(checkpoint.get(key) != value for key, value in binding.items()):
                    raise ProviderExecutionError("UAT_FUTURE_CLAIM_CHECKPOINT_BINDING_MISMATCH")
                if checkpoint.get("state") == "COMPLETED":
                    completed += 1
                    continue
                raise ProviderExecutionError(
                    str(
                        checkpoint.get(
                            "error_code", "UAT_FUTURE_CLAIM_PREVIOUS_ATTEMPT_BLOCKS_RESUME"
                        )
                    )
                )
            if int(str(manifest["request_count"])) >= self.max_requests:
                raise ProviderExecutionError("UAT_FUTURE_CLAIM_REQUEST_BUDGET_EXCEEDED")
            checkpoint = {
                "state": "UNKNOWN_OUTCOME",
                **binding,
                "idempotency_key": f"uat-future-claim-{case_id}",
                "automatic_retries": 0,
            }
            manifest["request_count"] = int(str(manifest["request_count"])) + 1
            self.checkpoints.save(self.namespace, "_manifest", manifest)
            self.checkpoints.save(self.namespace, case_id, checkpoint)
            try:
                response = self.transport.generate_claims(
                    dict(item["contract"]),
                    f"uat-future-claim-{case_id}",
                    self.timeout_seconds,
                )
                if not isinstance(response, Mapping):
                    raise ProviderExecutionError(
                        "UAT_FUTURE_CLAIM_RESPONSE_INVALID", outcome_unknown=False
                    )
                validated = validate_claim_response(
                    response,
                    item["envelopes"],
                    allow_cross_document=bool(binding["allow_cross_document"]),
                )
                audit = build_audit_manifest(
                    test_case_id=case_id,
                    question_sha256=str(binding["question_sha256"]),
                    bundle_sha256=str(binding["bundle_sha256"]),
                    evidence=item["envelopes"],
                    validated_response=response,
                    allow_cross_document=bool(binding["allow_cross_document"]),
                )
                stored_audit = self.artifacts.persist_claim_audit_manifest(case_id, audit)
                stored_result = self.artifacts.persist_claim_result(
                    case_id,
                    {
                        "revision": "future-uat-claim-result:v1",
                        "test_case_id": case_id,
                        "status": validated["status"],
                        "answer": validated["answer"],
                        "citation_ids": validated["citation_ids"],
                        "claim_snapshot_sha256": validated["claim_snapshot_sha256"],
                        "locator_grounded": validated["locator_grounded"],
                        "audit_ref": stored_audit["audit_ref"],
                        "audit_sha256": stored_audit["audit_sha256"],
                        "user_review_status": "PENDING_USER_RESULT_REVIEW",
                    },
                )
            except Exception as error:
                code = (
                    error.code
                    if isinstance(error, ProviderExecutionError)
                    else "UAT_FUTURE_CLAIM_FAILURE"
                )
                unknown = (
                    isinstance(error, ProviderExecutionError) and error.outcome_unknown is not False
                )
                checkpoint.update(
                    state="UNKNOWN_OUTCOME" if unknown else "FAILED",
                    error_code=code,
                    automatic_retries=0,
                )
                self.checkpoints.save(self.namespace, case_id, checkpoint)
                raise ProviderExecutionError(code, outcome_unknown=unknown) from None
            checkpoint.update(
                state="COMPLETED",
                audit_ref=stored_audit["audit_ref"],
                audit_sha256=stored_audit["audit_sha256"],
                result_ref=stored_result["result_ref"],
                result_sha256=stored_result["result_sha256"],
                citation_gate_passed=True,
                expected_evidence_covered=True,
                locator_grounded=validated["locator_grounded"],
            )
            self.checkpoints.save(self.namespace, case_id, checkpoint)
            completed += 1
        coverage = self._ensure_coverage(prepared, manifest_parameters)
        return {
            "revision": self.revision,
            "case_count": len(prepared),
            "request_count": int(str(manifest["request_count"])),
            "completed_count": completed,
            "failed_count": 0,
            "unknown_count": 0,
            "automatic_retries": 0,
            "user_result_review_required": True,
            **coverage,
            "historical_artifacts_mutated": False,
            "content_output": False,
        }


class FutureErrorCaseRetestRunner(FutureUatClaimRunner):
    """Independent runner namespace reserved for dynamic error-case retests."""

    revision = "uat-future-error-retest-runner:v1"
    namespace = "uat_future_error_retest_v1"
    artifact_revision = "error-retest-v1"


class FutureErrorCaseRetestV2Runner(FutureUatClaimRunner):
    """Render-proof input revision for future error-case retests."""

    revision = "uat-future-error-retest-runner:v2"
    namespace = "uat_future_error_retest_v2"
    artifact_revision = "error-retest-v2"


class FutureErrorCaseRetestV3Runner(FutureUatClaimRunner):
    revision = "uat-future-error-retest-runner:v3"
    namespace = "uat_future_error_retest_v3"
    artifact_revision = "error-retest-v3"


def require_future_case_egress(
    cases: Sequence[Mapping[str, object]],
    *,
    outbound_ai_allowed: bool,
    allowed_classifications: Sequence[str],
    approved_processing_regions: Sequence[str],
) -> None:
    classifications = []
    for case in cases:
        classification = case.get("source_classification")
        if not isinstance(classification, str) or not classification:
            raise ExecutionApprovalRequired("UAT_FUTURE_CLAIM_CLASSIFICATION_INVALID")
        classifications.append(classification)
    require_configured_provider_egress(
        outbound_ai_allowed=outbound_ai_allowed,
        allowed_classifications=allowed_classifications,
        approved_processing_regions=approved_processing_regions,
        classifications=classifications,
    )


def _raise_future_evidence_schema() -> Any:
    raise UatRemediationError("UAT_FUTURE_EVIDENCE_SCHEMA_INVALID")
