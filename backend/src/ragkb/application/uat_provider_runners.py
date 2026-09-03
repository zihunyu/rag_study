"""Budgeted two-stage real-UAT Reranker and LLM runners."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence

from ragkb.contracts.provider_execution import (
    CheckpointStorePort,
    ExecutionApprovalRequired,
    ProviderExecutionError,
    UatLlmTransportPort,
    UatRerankerTransportPort,
    UatResultStorePort,
)


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode(),
        usedforsecurity=False,
    ).hexdigest()


def _safe_failure(
    error: Exception, checkpoint: dict[str, object], default_code: str
) -> tuple[str, dict[str, object]]:
    code = error.code if isinstance(error, ProviderExecutionError) else default_code
    if isinstance(error, ProviderExecutionError) and error.outcome_unknown is not None:
        unknown = error.outcome_unknown
    else:
        unknown = checkpoint.get("state") == "UNKNOWN_OUTCOME"
    failed = dict(checkpoint)
    failed.update(
        state="UNKNOWN_OUTCOME" if unknown else "FAILED",
        error_code=code,
        automatic_retries=0,
    )
    if isinstance(error, ProviderExecutionError) and error.status_code is not None:
        failed["http_status"] = error.status_code
    if isinstance(error, ProviderExecutionError) and error.provider_error_code:
        failed["provider_error_code"] = error.provider_error_code
    if isinstance(error, ProviderExecutionError) and error.provider_error_type:
        failed["provider_error_type"] = error.provider_error_type
    if isinstance(error, ProviderExecutionError) and error.trace_id_hash:
        failed["trace_id_hash"] = error.trace_id_hash
    return code, failed


_LOCATOR_KEYS = frozenset(
    {
        "page",
        "slide",
        "sheet",
        "cell_range",
        "row",
        "bbox",
        "char_range",
        "start_time",
        "end_time",
    }
)


def _safe_locator(locator: object) -> bool:
    if not isinstance(locator, Mapping) or not locator or not set(locator).issubset(_LOCATOR_KEYS):
        return False
    if any(
        key in locator
        and (
            not isinstance(locator[key], int) or isinstance(locator[key], bool) or locator[key] < 1
        )
        for key in ("page", "slide", "row")
    ):
        return False
    if any(
        key in locator and (not isinstance(locator[key], str) or not locator[key].strip())
        for key in ("sheet", "cell_range")
    ):
        return False
    bbox = locator.get("bbox")
    if bbox is not None and (
        not isinstance(bbox, Sequence)
        or isinstance(bbox, (str, bytes))
        or len(bbox) != 4
        or any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            for value in bbox
        )
    ):
        return False
    char_range = locator.get("char_range")
    if char_range is not None and (
        not isinstance(char_range, Sequence)
        or isinstance(char_range, (str, bytes))
        or len(char_range) != 2
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in char_range
        )
        or char_range[1] < char_range[0]
    ):
        return False
    for key in ("start_time", "end_time"):
        value = locator.get(key)
        if value is not None and (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0
        ):
            return False
    if (
        "start_time" in locator
        and "end_time" in locator
        and locator["end_time"] < locator["start_time"]
    ):
        return False
    return True


def _validate_bundle(bundle: Mapping[str, object]) -> str:
    candidate_id = bundle.get("candidate_id")
    documents = bundle.get("documents")
    question = bundle.get("question")
    revision = bundle.get("revision")
    expected_positive = bundle.get("expected_positive_evidence_id")
    if (
        not isinstance(candidate_id, str)
        or len(candidate_id) != 20
        or not isinstance(question, str)
        or not question.strip()
        or not isinstance(revision, str)
        or not revision
        or not isinstance(expected_positive, str)
        or not expected_positive
        or not isinstance(documents, Sequence)
        or isinstance(documents, (str, bytes))
        or len(documents) != 4
    ):
        raise ProviderExecutionError("UAT_BUNDLE_SCHEMA_INVALID")
    evidence_ids: list[str] = []
    for document in documents:
        if not isinstance(document, Mapping):
            raise ProviderExecutionError("UAT_BUNDLE_DOCUMENT_SCHEMA_INVALID")
        evidence_id = document.get("evidence_id")
        content = document.get("content")
        content_hash = document.get("content_sha256")
        role = document.get("role")
        if (
            not isinstance(evidence_id, str)
            or not evidence_id
            or not isinstance(content, str)
            or not content.strip()
            or not isinstance(content_hash, str)
            or hashlib.sha256(content.encode(), usedforsecurity=False).hexdigest() != content_hash
            or not _safe_locator(document.get("locator"))
            or role not in {"positive", "distractor"}
        ):
            raise ProviderExecutionError("UAT_BUNDLE_DOCUMENT_SCHEMA_INVALID")
        evidence_ids.append(evidence_id)
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ProviderExecutionError("UAT_BUNDLE_EVIDENCE_ID_DUPLICATE")
    positive = [
        document
        for document in documents
        if isinstance(document, Mapping) and document.get("role") == "positive"
    ]
    distractors = [
        document
        for document in documents
        if isinstance(document, Mapping) and document.get("role") == "distractor"
    ]
    if len(positive) != 1 or len(distractors) != 3:
        raise ProviderExecutionError("UAT_BUNDLE_DOCUMENT_ROLES_INVALID")
    if positive[0].get("evidence_id") != expected_positive:
        raise ProviderExecutionError("UAT_BUNDLE_EXPECTED_POSITIVE_MISMATCH")
    return candidate_id


def _validated_bundles(bundles: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    if len(bundles) != 78:
        raise ProviderExecutionError("UAT_BUNDLE_COUNT_MISMATCH")
    ids = [_validate_bundle(bundle) for bundle in bundles]
    if len(set(ids)) != 78:
        raise ProviderExecutionError("UAT_BUNDLE_ID_DUPLICATE")
    return list(bundles)


def _bundle_documents(bundle: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = bundle.get("documents")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ProviderExecutionError("UAT_BUNDLE_DOCUMENTS_INVALID")
    documents = [document for document in raw if isinstance(document, Mapping)]
    if len(documents) != len(raw):
        raise ProviderExecutionError("UAT_BUNDLE_DOCUMENTS_INVALID")
    return documents


def _bundle_binding(bundle: Mapping[str, object]) -> dict[str, object]:
    documents = _bundle_documents(bundle)
    question = str(bundle["question"])
    canonical_bundle = (
        json.dumps(dict(bundle), ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    return {
        "candidate_id": bundle["candidate_id"],
        "question_sha256": hashlib.sha256(question.encode(), usedforsecurity=False).hexdigest(),
        "documents": [
            {
                "evidence_id": document["evidence_id"],
                "content_sha256": document["content_sha256"],
                "locator_sha256": _canonical_hash(document["locator"]),
                "role": document["role"],
            }
            for document in documents
        ],
        "expected_positive_evidence_id": bundle["expected_positive_evidence_id"],
        "bundle_sha256": hashlib.sha256(canonical_bundle, usedforsecurity=False).hexdigest(),
        "bundle_revision": bundle["revision"],
    }


def _validate_manifest_parameters(
    manifest: Mapping[str, object], expected: Mapping[str, object], code: str
) -> None:
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ProviderExecutionError(code)


def build_combined_reranker_gate(
    bundles: Sequence[Mapping[str, object]],
    records: Mapping[str, Mapping[str, object]],
    provenance: Mapping[str, str],
    source_checkpoint_hashes: Mapping[str, str],
    *,
    revision: str = "uat-combined-reranker-gate:v3",
    required_sources: frozenset[str] = frozenset({"v1", "v2", "v3"}),
) -> dict[str, object]:
    items = _validated_bundles(bundles)
    bindings = [_bundle_binding(bundle) for bundle in items]
    combined_results: list[dict[str, object]] = []
    for bundle, binding in zip(items, bindings, strict=True):
        candidate_id = str(bundle["candidate_id"])
        checkpoint = records.get(candidate_id)
        source = provenance.get(candidate_id)
        documents = _bundle_documents(bundle)
        evidence_ids = [str(document["evidence_id"]) for document in documents]
        ranked_ids = checkpoint.get("ranked_evidence_ids") if checkpoint is not None else None
        positive_rank = checkpoint.get("positive_rank") if checkpoint is not None else None
        if (
            checkpoint is None
            or source not in required_sources
            or checkpoint.get("state") != "COMPLETED"
            or checkpoint.get("gate_passed") is not True
            or checkpoint.get("candidate_id") != candidate_id
            or checkpoint.get("bundle_document_ids") != evidence_ids
            or checkpoint.get("expected_positive_evidence_id")
            != bundle["expected_positive_evidence_id"]
            or checkpoint.get("question_sha256") != binding["question_sha256"]
            or checkpoint.get("bundle_sha256") != binding["bundle_sha256"]
            or checkpoint.get("bundle_revision") != binding["bundle_revision"]
            or not isinstance(ranked_ids, Sequence)
            or isinstance(ranked_ids, (str, bytes))
            or len(ranked_ids) != 4
            or any(not isinstance(value, str) for value in ranked_ids)
            or len(set(ranked_ids)) != 4
            or set(ranked_ids) != set(evidence_ids)
            or not isinstance(positive_rank, int)
            or isinstance(positive_rank, bool)
            or positive_rank
            != list(ranked_ids).index(str(bundle["expected_positive_evidence_id"])) + 1
            or positive_rank > 2
        ):
            raise ProviderExecutionError("UAT_COMBINED_RERANKER_GATE_INVALID")
        combined_results.append(
            {
                "candidate_id": candidate_id,
                "ranked_evidence_ids": list(ranked_ids),
                "positive_rank": positive_rank,
                "gate_passed": True,
                "source_checkpoint": source,
                "bundle_sha256": binding["bundle_sha256"],
                "question_sha256": binding["question_sha256"],
            }
        )
    if set(source_checkpoint_hashes) != required_sources or any(
        not isinstance(value, str) or len(value) != 64
        for value in source_checkpoint_hashes.values()
    ):
        raise ProviderExecutionError("UAT_COMBINED_RERANKER_SOURCE_HASHES_INVALID")
    return {
        "revision": revision,
        "candidate_count": 78,
        "gate_passed_count": 78,
        "positive_top_k": 2,
        "bundle_snapshot_hash": _canonical_hash(bindings),
        "results": combined_results,
        "source_checkpoint_hashes": dict(source_checkpoint_hashes),
        "llm_execution_unlocked": True,
        "content_output": False,
    }


class UatRerankerExecutionRunner:
    revision = "uat-reranker-runner:v2"

    def __init__(
        self,
        transport: UatRerankerTransportPort,
        checkpoints: CheckpointStorePort,
        *,
        external_call_approved: bool,
        max_requests: int = 78,
        positive_top_k: int = 2,
        timeout_seconds: float = 60,
    ) -> None:
        self.transport = transport
        self.checkpoints = checkpoints
        self.external_call_approved = external_call_approved
        self.max_requests = max_requests
        self.positive_top_k = positive_top_k
        self.timeout_seconds = timeout_seconds

    def run(self, bundles: Sequence[Mapping[str, object]]) -> dict[str, object]:
        if self.transport.real_network and not self.external_call_approved:
            raise ExecutionApprovalRequired("UAT_RERANKER_EXECUTION_APPROVAL_REQUIRED")
        if self.max_requests != 78 or self.positive_top_k != 2:
            raise ProviderExecutionError("UAT_RERANKER_EXECUTION_PARAMETERS_INVALID")
        items = _validated_bundles(bundles)
        snapshot = [_bundle_binding(bundle) for bundle in items]
        binding_by_candidate = {str(binding["candidate_id"]): binding for binding in snapshot}
        snapshot_hash = _canonical_hash(snapshot)
        manifest_parameters = {
            "revision": self.revision,
            "snapshot_hash": snapshot_hash,
            "candidate_count": 78,
            "max_requests": self.max_requests,
            "positive_top_k": self.positive_top_k,
            "automatic_retries": 0,
            "global_failure_policy": "STOP_ALL_AND_DO_NOT_START_LLM",
        }
        manifest = self.checkpoints.get("uat_reranker", "_manifest")
        if manifest is None:
            manifest = {
                **manifest_parameters,
                "request_count": 0,
            }
            self.checkpoints.save("uat_reranker", "_manifest", manifest)
        else:
            _validate_manifest_parameters(
                manifest,
                manifest_parameters,
                "UAT_RERANKER_SNAPSHOT_OR_PARAMETERS_MISMATCH",
            )
        completed = 0
        for bundle in items:
            candidate_id = str(bundle["candidate_id"])
            checkpoint = self.checkpoints.get("uat_reranker", candidate_id)
            if checkpoint is not None:
                binding = binding_by_candidate[candidate_id]
                expected_checkpoint_binding = {
                    "candidate_id": candidate_id,
                    "bundle_document_ids": [
                        str(document["evidence_id"]) for document in _bundle_documents(bundle)
                    ],
                    "expected_positive_evidence_id": bundle["expected_positive_evidence_id"],
                    "question_sha256": binding["question_sha256"],
                    "bundle_sha256": binding["bundle_sha256"],
                    "bundle_revision": binding["bundle_revision"],
                }
                if any(
                    checkpoint.get(key) != value
                    for key, value in expected_checkpoint_binding.items()
                ):
                    raise ProviderExecutionError("UAT_RERANKER_CHECKPOINT_BINDING_MISMATCH")
                if checkpoint.get("state") == "COMPLETED" and checkpoint.get("gate_passed") is True:
                    completed += 1
                    continue
                raise ProviderExecutionError(
                    str(checkpoint.get("error_code", "UAT_RERANKER_PREVIOUS_ATTEMPT_BLOCKS_RESUME"))
                )
            if int(str(manifest["request_count"])) >= self.max_requests:
                raise ProviderExecutionError("UAT_RERANKER_REQUEST_BUDGET_EXCEEDED")
            documents = [dict(document) for document in _bundle_documents(bundle)]
            evidence_ids = [str(document["evidence_id"]) for document in documents]
            positive_id = str(bundle["expected_positive_evidence_id"])
            positive_index = evidence_ids.index(positive_id)
            checkpoint = {
                "state": "UNKNOWN_OUTCOME",
                "candidate_id": candidate_id,
                "bundle_document_ids": evidence_ids,
                "expected_positive_evidence_id": positive_id,
                "question_sha256": binding_by_candidate[candidate_id]["question_sha256"],
                "bundle_sha256": binding_by_candidate[candidate_id]["bundle_sha256"],
                "bundle_revision": binding_by_candidate[candidate_id]["bundle_revision"],
                "idempotency_key": f"uat-reranker-{candidate_id}",
                "automatic_retries": 0,
            }
            manifest["request_count"] = int(str(manifest["request_count"])) + 1
            self.checkpoints.save("uat_reranker", "_manifest", manifest)
            self.checkpoints.save("uat_reranker", candidate_id, checkpoint)
            try:
                order = list(
                    self.transport.rerank(
                        str(bundle["question"]),
                        [str(document["content"]) for document in documents],
                        len(documents),
                        f"uat-reranker-{candidate_id}",
                        self.timeout_seconds,
                    )
                )
                if (
                    len(order) != len(documents)
                    or len(set(order)) != len(order)
                    or set(order) != set(range(len(documents)))
                ):
                    raise ProviderExecutionError(
                        "UAT_RERANKER_RESPONSE_INDEX_INVALID", outcome_unknown=False
                    )
                gate_passed = positive_index in order[: self.positive_top_k]
                ranked_ids = [evidence_ids[index] for index in order]
                checkpoint.update(
                    ranked_evidence_ids=ranked_ids,
                    positive_rank=order.index(positive_index) + 1,
                    response_index_count=len(order),
                    gate_passed=gate_passed,
                )
                self.checkpoints.save("uat_reranker", candidate_id, checkpoint)
                if not gate_passed:
                    raise ProviderExecutionError(
                        "UAT_RERANKER_POSITIVE_NOT_IN_TOP_K", outcome_unknown=False
                    )
            except Exception as error:
                code, failed = _safe_failure(error, checkpoint, "UAT_RERANKER_FAILURE")
                self.checkpoints.save("uat_reranker", candidate_id, failed)
                raise ProviderExecutionError(code) from None
            checkpoint.update(
                state="COMPLETED",
            )
            self.checkpoints.save("uat_reranker", candidate_id, checkpoint)
            completed += 1
        return {
            "candidate_count": 78,
            "request_count": int(str(manifest["request_count"])),
            "completed_count": completed,
            "gate_passed_count": completed,
            "automatic_retries": 0,
            "llm_execution_unlocked": completed == 78,
            "content_output": False,
            "secret_values_in_output": False,
        }


class UatRerankerDiagnosticV2Runner:
    revision = "uat-reranker-diagnostic-runner:v2"
    namespace = "uat_reranker_v2"

    def __init__(
        self,
        transport: UatRerankerTransportPort,
        checkpoints: CheckpointStorePort,
        *,
        external_call_approved: bool,
        max_requests: int = 1,
        positive_top_k: int = 2,
        timeout_seconds: float = 60,
    ) -> None:
        self.transport = transport
        self.checkpoints = checkpoints
        self.external_call_approved = external_call_approved
        self.max_requests = max_requests
        self.positive_top_k = positive_top_k
        self.timeout_seconds = timeout_seconds

    def run(self, bundle: Mapping[str, object]) -> dict[str, object]:
        if self.transport.real_network and not self.external_call_approved:
            raise ExecutionApprovalRequired("UAT_RERANKER_V2_EXECUTION_APPROVAL_REQUIRED")
        if self.max_requests != 1 or self.positive_top_k != 2:
            raise ProviderExecutionError("UAT_RERANKER_V2_EXECUTION_PARAMETERS_INVALID")
        candidate_id = _validate_bundle(bundle)
        binding = _bundle_binding(bundle)
        snapshot_hash = _canonical_hash(binding)
        manifest_parameters = {
            "revision": self.revision,
            "snapshot_hash": snapshot_hash,
            "candidate_count": 1,
            "max_requests": 1,
            "positive_top_k": 2,
            "automatic_retries": 0,
            "llm_allowed": False,
            "diagnostic_only": True,
        }
        manifest = self.checkpoints.get(self.namespace, "_manifest")
        if manifest is None:
            manifest = {**manifest_parameters, "request_count": 0}
            self.checkpoints.save(self.namespace, "_manifest", manifest)
        else:
            _validate_manifest_parameters(
                manifest,
                manifest_parameters,
                "UAT_RERANKER_V2_SNAPSHOT_OR_PARAMETERS_MISMATCH",
            )
        documents = [dict(document) for document in _bundle_documents(bundle)]
        evidence_ids = [str(document["evidence_id"]) for document in documents]
        positive_id = str(bundle["expected_positive_evidence_id"])
        positive_index = evidence_ids.index(positive_id)
        checkpoint = self.checkpoints.get(self.namespace, candidate_id)
        if checkpoint is not None:
            expected_binding = {
                "candidate_id": candidate_id,
                "bundle_document_ids": evidence_ids,
                "expected_positive_evidence_id": positive_id,
                "question_sha256": binding["question_sha256"],
                "bundle_sha256": binding["bundle_sha256"],
                "bundle_revision": binding["bundle_revision"],
            }
            if any(checkpoint.get(key) != value for key, value in expected_binding.items()):
                raise ProviderExecutionError("UAT_RERANKER_V2_CHECKPOINT_BINDING_MISMATCH")
            if checkpoint.get("state") == "COMPLETED" and checkpoint.get("gate_passed") is True:
                return {
                    "candidate_count": 1,
                    "request_count": int(str(manifest["request_count"])),
                    "completed_count": 1,
                    "gate_passed_count": 1,
                    "automatic_retries": 0,
                    "llm_request_count": 0,
                    "diagnostic_only": True,
                    "content_output": False,
                }
            raise ProviderExecutionError(
                str(checkpoint.get("error_code", "UAT_RERANKER_V2_PREVIOUS_ATTEMPT_BLOCKS_RESUME"))
            )
        if int(str(manifest["request_count"])) >= self.max_requests:
            raise ProviderExecutionError("UAT_RERANKER_V2_REQUEST_BUDGET_EXCEEDED")
        checkpoint = {
            "state": "UNKNOWN_OUTCOME",
            "candidate_id": candidate_id,
            "bundle_document_ids": evidence_ids,
            "expected_positive_evidence_id": positive_id,
            "question_sha256": binding["question_sha256"],
            "bundle_sha256": binding["bundle_sha256"],
            "bundle_revision": binding["bundle_revision"],
            "idempotency_key": f"uat-reranker-v2-{candidate_id}",
            "automatic_retries": 0,
        }
        manifest["request_count"] = int(str(manifest["request_count"])) + 1
        self.checkpoints.save(self.namespace, "_manifest", manifest)
        self.checkpoints.save(self.namespace, candidate_id, checkpoint)
        try:
            order = list(
                self.transport.rerank(
                    str(bundle["question"]),
                    [str(document["content"]) for document in documents],
                    len(documents),
                    f"uat-reranker-v2-{candidate_id}",
                    self.timeout_seconds,
                )
            )
            if (
                len(order) != len(documents)
                or any(not isinstance(index, int) or isinstance(index, bool) for index in order)
                or len(set(order)) != len(order)
                or set(order) != set(range(len(documents)))
            ):
                raise ProviderExecutionError(
                    "UAT_RERANKER_V2_RESPONSE_INDEX_INVALID", outcome_unknown=False
                )
            gate_passed = positive_index in order[: self.positive_top_k]
            checkpoint.update(
                ranked_evidence_ids=[evidence_ids[index] for index in order],
                positive_rank=order.index(positive_index) + 1,
                response_index_count=len(order),
                gate_passed=gate_passed,
            )
            self.checkpoints.save(self.namespace, candidate_id, checkpoint)
            if not gate_passed:
                raise ProviderExecutionError(
                    "UAT_RERANKER_V2_POSITIVE_NOT_IN_TOP_K", outcome_unknown=False
                )
        except Exception as error:
            code, failed = _safe_failure(error, checkpoint, "UAT_RERANKER_V2_FAILURE")
            self.checkpoints.save(self.namespace, candidate_id, failed)
            raise ProviderExecutionError(code) from None
        checkpoint["state"] = "COMPLETED"
        self.checkpoints.save(self.namespace, candidate_id, checkpoint)
        return {
            "candidate_count": 1,
            "request_count": int(str(manifest["request_count"])),
            "completed_count": 1,
            "gate_passed_count": 1,
            "automatic_retries": 0,
            "llm_request_count": 0,
            "diagnostic_only": True,
            "content_output": False,
        }


class UatRerankerContinuationV3Runner:
    revision = "uat-reranker-continuation-runner:v3"
    namespace = "uat_reranker_v3"
    candidate_count = 76
    code_prefix = "UAT_RERANKER_V3"
    idempotency_prefix = "uat-reranker-v3"

    def __init__(
        self,
        transport: UatRerankerTransportPort,
        checkpoints: CheckpointStorePort,
        *,
        external_call_approved: bool,
        max_requests: int | None = None,
        positive_top_k: int = 2,
        timeout_seconds: float = 60,
    ) -> None:
        self.transport = transport
        self.checkpoints = checkpoints
        self.external_call_approved = external_call_approved
        self.max_requests = self.candidate_count if max_requests is None else max_requests
        self.positive_top_k = positive_top_k
        self.timeout_seconds = timeout_seconds

    def run(self, bundles: Sequence[Mapping[str, object]]) -> dict[str, object]:
        if self.transport.real_network and not self.external_call_approved:
            raise ExecutionApprovalRequired(f"{self.code_prefix}_EXECUTION_APPROVAL_REQUIRED")
        if self.max_requests != self.candidate_count or self.positive_top_k != 2:
            raise ProviderExecutionError(f"{self.code_prefix}_EXECUTION_PARAMETERS_INVALID")
        if len(bundles) != self.candidate_count:
            raise ProviderExecutionError(f"{self.code_prefix}_BUNDLE_COUNT_MISMATCH")
        candidate_ids = [_validate_bundle(bundle) for bundle in bundles]
        if len(set(candidate_ids)) != self.candidate_count:
            raise ProviderExecutionError(f"{self.code_prefix}_BUNDLE_ID_DUPLICATE")
        items = list(bundles)
        bindings = [_bundle_binding(bundle) for bundle in items]
        binding_by_candidate = {str(binding["candidate_id"]): binding for binding in bindings}
        manifest_parameters = {
            "revision": self.revision,
            "snapshot_hash": _canonical_hash(bindings),
            "candidate_count": self.candidate_count,
            "max_requests": self.candidate_count,
            "positive_top_k": 2,
            "automatic_retries": 0,
            "global_failure_policy": "STOP_ALL_AND_DO_NOT_START_LLM",
        }
        manifest = self.checkpoints.get(self.namespace, "_manifest")
        if manifest is None:
            manifest = {**manifest_parameters, "request_count": 0}
            self.checkpoints.save(self.namespace, "_manifest", manifest)
        else:
            _validate_manifest_parameters(
                manifest,
                manifest_parameters,
                f"{self.code_prefix}_SNAPSHOT_OR_PARAMETERS_MISMATCH",
            )
        completed = 0
        for bundle in items:
            candidate_id = str(bundle["candidate_id"])
            documents = [dict(document) for document in _bundle_documents(bundle)]
            evidence_ids = [str(document["evidence_id"]) for document in documents]
            positive_id = str(bundle["expected_positive_evidence_id"])
            binding = binding_by_candidate[candidate_id]
            checkpoint = self.checkpoints.get(self.namespace, candidate_id)
            if checkpoint is not None:
                expected_binding = {
                    "candidate_id": candidate_id,
                    "bundle_document_ids": evidence_ids,
                    "expected_positive_evidence_id": positive_id,
                    "question_sha256": binding["question_sha256"],
                    "bundle_sha256": binding["bundle_sha256"],
                    "bundle_revision": binding["bundle_revision"],
                }
                if any(checkpoint.get(key) != value for key, value in expected_binding.items()):
                    raise ProviderExecutionError(f"{self.code_prefix}_CHECKPOINT_BINDING_MISMATCH")
                if checkpoint.get("state") == "COMPLETED" and checkpoint.get("gate_passed") is True:
                    completed += 1
                    continue
                raise ProviderExecutionError(
                    str(
                        checkpoint.get(
                            "error_code", f"{self.code_prefix}_PREVIOUS_ATTEMPT_BLOCKS_RESUME"
                        )
                    )
                )
            if int(str(manifest["request_count"])) >= self.max_requests:
                raise ProviderExecutionError(f"{self.code_prefix}_REQUEST_BUDGET_EXCEEDED")
            positive_index = evidence_ids.index(positive_id)
            checkpoint = {
                "state": "UNKNOWN_OUTCOME",
                "candidate_id": candidate_id,
                "bundle_document_ids": evidence_ids,
                "expected_positive_evidence_id": positive_id,
                "question_sha256": binding["question_sha256"],
                "bundle_sha256": binding["bundle_sha256"],
                "bundle_revision": binding["bundle_revision"],
                "idempotency_key": f"{self.idempotency_prefix}-{candidate_id}",
                "automatic_retries": 0,
            }
            manifest["request_count"] = int(str(manifest["request_count"])) + 1
            self.checkpoints.save(self.namespace, "_manifest", manifest)
            self.checkpoints.save(self.namespace, candidate_id, checkpoint)
            try:
                order = list(
                    self.transport.rerank(
                        str(bundle["question"]),
                        [str(document["content"]) for document in documents],
                        len(documents),
                        f"{self.idempotency_prefix}-{candidate_id}",
                        self.timeout_seconds,
                    )
                )
                if (
                    len(order) != len(documents)
                    or any(not isinstance(index, int) or isinstance(index, bool) for index in order)
                    or len(set(order)) != len(order)
                    or set(order) != set(range(len(documents)))
                ):
                    raise ProviderExecutionError(
                        f"{self.code_prefix}_RESPONSE_INDEX_INVALID", outcome_unknown=False
                    )
                gate_passed = positive_index in order[: self.positive_top_k]
                checkpoint.update(
                    ranked_evidence_ids=[evidence_ids[index] for index in order],
                    positive_rank=order.index(positive_index) + 1,
                    response_index_count=len(order),
                    gate_passed=gate_passed,
                )
                self.checkpoints.save(self.namespace, candidate_id, checkpoint)
                if not gate_passed:
                    raise ProviderExecutionError(
                        f"{self.code_prefix}_POSITIVE_NOT_IN_TOP_K", outcome_unknown=False
                    )
            except Exception as error:
                code, failed = _safe_failure(error, checkpoint, f"{self.code_prefix}_FAILURE")
                self.checkpoints.save(self.namespace, candidate_id, failed)
                raise ProviderExecutionError(code) from None
            checkpoint["state"] = "COMPLETED"
            self.checkpoints.save(self.namespace, candidate_id, checkpoint)
            completed += 1
        return {
            "candidate_count": self.candidate_count,
            "request_count": int(str(manifest["request_count"])),
            "completed_count": completed,
            "gate_passed_count": completed,
            "automatic_retries": 0,
            "combined_gate_ready": completed == self.candidate_count,
            "llm_request_count": 0,
            "content_output": False,
        }


class UatRerankerSystematicV4Runner(UatRerankerContinuationV3Runner):
    revision = "uat-reranker-systematic-runner:v4"
    namespace = "uat_reranker_v4"
    candidate_count = 75
    code_prefix = "UAT_RERANKER_V4"
    idempotency_prefix = "uat-reranker-v4"


class UatRerankerSystematicV5Runner(UatRerankerContinuationV3Runner):
    """Run only the 39 user-approved, two-term v5 systematic revisions."""

    revision = "uat-reranker-systematic-runner:v5"
    namespace = "uat_reranker_v5"
    candidate_count = 39
    code_prefix = "UAT_RERANKER_V5"
    idempotency_prefix = "uat-reranker-v5"


class UatLlmExecutionRunner:
    revision = "uat-llm-runner:v1"
    _allowed_statuses = frozenset(
        {"answered", "insufficient_evidence", "needs_clarification", "conflicting_evidence"}
    )

    def __init__(
        self,
        transport: UatLlmTransportPort,
        reranker_checkpoints: CheckpointStorePort,
        llm_checkpoints: CheckpointStorePort,
        result_store: UatResultStorePort,
        *,
        external_call_approved: bool,
        max_requests: int = 78,
        reranker_top_k: int = 2,
        timeout_seconds: float = 120,
    ) -> None:
        self.transport = transport
        self.reranker_checkpoints = reranker_checkpoints
        self.llm_checkpoints = llm_checkpoints
        self.result_store = result_store
        self.external_call_approved = external_call_approved
        self.max_requests = max_requests
        self.reranker_top_k = reranker_top_k
        self.timeout_seconds = timeout_seconds

    def run(self, bundles: Sequence[Mapping[str, object]]) -> dict[str, object]:
        if self.transport.real_network and not self.external_call_approved:
            raise ExecutionApprovalRequired("UAT_LLM_EXECUTION_APPROVAL_REQUIRED")
        if self.max_requests != 78 or self.reranker_top_k != 2:
            raise ProviderExecutionError("UAT_LLM_EXECUTION_PARAMETERS_INVALID")
        items = _validated_bundles(bundles)
        bundle_bindings = [_bundle_binding(bundle) for bundle in items]
        binding_by_candidate = {
            str(binding["candidate_id"]): binding for binding in bundle_bindings
        }
        expected_reranker_snapshot_hash = _canonical_hash(bundle_bindings)
        reranker_manifest = self.reranker_checkpoints.get("uat_reranker", "_manifest")
        if reranker_manifest is None:
            raise ProviderExecutionError("UAT_LLM_RERANKER_GLOBAL_GATE_NOT_MET")
        _validate_manifest_parameters(
            reranker_manifest,
            {
                "revision": UatRerankerExecutionRunner.revision,
                "snapshot_hash": expected_reranker_snapshot_hash,
                "candidate_count": 78,
                "max_requests": 78,
                "positive_top_k": self.reranker_top_k,
                "automatic_retries": 0,
                "global_failure_policy": "STOP_ALL_AND_DO_NOT_START_LLM",
                "request_count": 78,
            },
            "UAT_LLM_RERANKER_GLOBAL_GATE_NOT_MET",
        )
        reranker_snapshot_hash = str(reranker_manifest["snapshot_hash"])
        reranker_bindings: dict[str, dict[str, object]] = {}
        for bundle in items:
            candidate_id = str(bundle["candidate_id"])
            checkpoint = self.reranker_checkpoints.get("uat_reranker", candidate_id)
            if (
                checkpoint is None
                or checkpoint.get("state") != "COMPLETED"
                or not checkpoint.get("gate_passed")
            ):
                raise ProviderExecutionError("UAT_LLM_RERANKER_GLOBAL_GATE_NOT_MET")
            documents = _bundle_documents(bundle)
            evidence_ids = [str(document["evidence_id"]) for document in documents]
            ranked_ids = checkpoint.get("ranked_evidence_ids")
            positive_rank = checkpoint.get("positive_rank")
            binding = binding_by_candidate[candidate_id]
            if (
                checkpoint.get("candidate_id") != candidate_id
                or checkpoint.get("bundle_document_ids") != evidence_ids
                or checkpoint.get("expected_positive_evidence_id")
                != bundle["expected_positive_evidence_id"]
                or checkpoint.get("question_sha256") != binding["question_sha256"]
                or checkpoint.get("bundle_sha256") != binding["bundle_sha256"]
                or checkpoint.get("bundle_revision") != binding["bundle_revision"]
                or not isinstance(ranked_ids, Sequence)
                or isinstance(ranked_ids, (str, bytes))
                or len(ranked_ids) != len(evidence_ids)
                or any(not isinstance(value, str) for value in ranked_ids)
                or len(set(ranked_ids)) != len(ranked_ids)
                or set(ranked_ids) != set(evidence_ids)
                or not isinstance(positive_rank, int)
                or isinstance(positive_rank, bool)
                or positive_rank
                != list(ranked_ids).index(str(bundle["expected_positive_evidence_id"])) + 1
                or positive_rank > self.reranker_top_k
            ):
                raise ProviderExecutionError("UAT_LLM_RERANKER_RESULT_INVALID")
            reranker_bindings[candidate_id] = {
                "ranked_evidence_ids": list(ranked_ids),
                "positive_rank": positive_rank,
                "gate_passed": True,
            }
        snapshot_hash = _canonical_hash(
            [
                {
                    "bundle": binding_by_candidate[str(bundle["candidate_id"])],
                    "reranker": reranker_bindings[str(bundle["candidate_id"])],
                }
                for bundle in items
            ]
        )
        manifest_parameters = {
            "revision": self.revision,
            "snapshot_hash": snapshot_hash,
            "candidate_count": 78,
            "max_requests": self.max_requests,
            "automatic_retries": 0,
            "user_result_review_required": True,
            "reranker_top_k": self.reranker_top_k,
            "reranker_snapshot_hash": reranker_snapshot_hash,
        }
        manifest = self.llm_checkpoints.get("uat_llm", "_manifest")
        if manifest is None:
            manifest = {
                **manifest_parameters,
                "request_count": 0,
            }
            self.llm_checkpoints.save("uat_llm", "_manifest", manifest)
        else:
            _validate_manifest_parameters(
                manifest,
                manifest_parameters,
                "UAT_LLM_SNAPSHOT_OR_PARAMETERS_MISMATCH",
            )
        completed = 0
        for bundle in items:
            candidate_id = str(bundle["candidate_id"])
            checkpoint = self.llm_checkpoints.get("uat_llm", candidate_id)
            if checkpoint is not None:
                ranked_binding = reranker_bindings[candidate_id]
                ranked_ids = ranked_binding["ranked_evidence_ids"]
                assert isinstance(ranked_ids, list)
                expected_checkpoint_binding = {
                    "candidate_id": candidate_id,
                    "selected_evidence_ids": ranked_ids[: self.reranker_top_k],
                    "expected_positive_evidence_id": bundle["expected_positive_evidence_id"],
                    "question_sha256": binding_by_candidate[candidate_id]["question_sha256"],
                    "bundle_sha256": binding_by_candidate[candidate_id]["bundle_sha256"],
                    "bundle_revision": binding_by_candidate[candidate_id]["bundle_revision"],
                    "reranker_snapshot_hash": reranker_snapshot_hash,
                    "reranker_result_sha256": _canonical_hash(ranked_binding),
                }
                if any(
                    checkpoint.get(key) != value
                    for key, value in expected_checkpoint_binding.items()
                ):
                    raise ProviderExecutionError("UAT_LLM_CHECKPOINT_BINDING_MISMATCH")
                if checkpoint.get("state") == "COMPLETED":
                    citations = checkpoint.get("citation_ids")
                    if (
                        checkpoint.get("citation_gate_passed") is not True
                        or checkpoint.get("expected_evidence_covered") is not True
                        or not isinstance(citations, Sequence)
                        or isinstance(citations, (str, bytes))
                        or any(
                            value not in ranked_ids[: self.reranker_top_k] for value in citations
                        )
                        or bundle["expected_positive_evidence_id"] not in citations
                    ):
                        raise ProviderExecutionError("UAT_LLM_CHECKPOINT_RESULT_INVALID")
                    completed += 1
                    continue
                raise ProviderExecutionError(
                    str(checkpoint.get("error_code", "UAT_LLM_PREVIOUS_ATTEMPT_BLOCKS_RESUME"))
                )
            if int(str(manifest["request_count"])) >= self.max_requests:
                raise ProviderExecutionError("UAT_LLM_REQUEST_BUDGET_EXCEEDED")
            reranked = self.reranker_checkpoints.get("uat_reranker", candidate_id)
            assert reranked is not None
            ranked_ids = reranker_bindings[candidate_id]["ranked_evidence_ids"]
            assert isinstance(ranked_ids, list)
            documents = [dict(document) for document in _bundle_documents(bundle)]
            by_id = {str(document["evidence_id"]): document for document in documents}
            selected_ids = [str(value) for value in ranked_ids[: self.reranker_top_k]]
            if any(evidence_id not in by_id for evidence_id in selected_ids):
                raise ProviderExecutionError("UAT_LLM_RERANKER_RESULT_INVALID")
            positive_id = str(bundle["expected_positive_evidence_id"])
            selected = [by_id[evidence_id] for evidence_id in selected_ids]
            checkpoint = {
                "state": "UNKNOWN_OUTCOME",
                "candidate_id": candidate_id,
                "selected_evidence_ids": selected_ids,
                "expected_positive_evidence_id": positive_id,
                "question_sha256": binding_by_candidate[candidate_id]["question_sha256"],
                "bundle_sha256": binding_by_candidate[candidate_id]["bundle_sha256"],
                "bundle_revision": binding_by_candidate[candidate_id]["bundle_revision"],
                "reranker_snapshot_hash": reranker_snapshot_hash,
                "reranker_result_sha256": _canonical_hash(reranker_bindings[candidate_id]),
                "idempotency_key": f"uat-llm-{candidate_id}",
                "automatic_retries": 0,
            }
            manifest["request_count"] = int(str(manifest["request_count"])) + 1
            self.llm_checkpoints.save("uat_llm", "_manifest", manifest)
            self.llm_checkpoints.save("uat_llm", candidate_id, checkpoint)
            try:
                result = self.transport.generate(
                    str(bundle["question"]),
                    [
                        {
                            "evidence_id": item["evidence_id"],
                            "locator": item["locator"],
                            "content": item["content"],
                        }
                        for item in selected
                    ],
                    f"uat-llm-{candidate_id}",
                    self.timeout_seconds,
                )
                status = result.get("status")
                answer = result.get("answer")
                citations = result.get("citation_ids")
                if (
                    status not in self._allowed_statuses
                    or not isinstance(answer, str)
                    or not answer.strip()
                    or not isinstance(citations, Sequence)
                    or isinstance(citations, (str, bytes))
                    or any(not isinstance(value, str) for value in citations)
                ):
                    raise ProviderExecutionError(
                        "UAT_LLM_RESPONSE_SCHEMA_INVALID", outcome_unknown=False
                    )
                citation_ids = [str(value) for value in citations]
                if (
                    len(set(citation_ids)) != len(citation_ids)
                    or any(value not in selected_ids for value in citation_ids)
                    or positive_id not in citation_ids
                ):
                    raise ProviderExecutionError(
                        "UAT_LLM_CITATION_GATE_FAILED", outcome_unknown=False
                    )
            except Exception as error:
                code, failed = _safe_failure(error, checkpoint, "UAT_LLM_FAILURE")
                self.llm_checkpoints.save("uat_llm", candidate_id, failed)
                raise ProviderExecutionError(code) from None
            stored = self.result_store.persist_result(
                candidate_id,
                {
                    "revision": "real-uat-result:v1",
                    "candidate_id": candidate_id,
                    "status": status,
                    "answer": answer,
                    "citation_ids": citation_ids,
                    "expected_positive_evidence_id": positive_id,
                    "locator_grounded": True,
                    "user_review_status": "PENDING_USER_RESULT_REVIEW",
                },
            )
            checkpoint.update(
                state="COMPLETED",
                result_ref=stored["result_ref"],
                result_sha256=stored["result_sha256"],
                answer_sha256=hashlib.sha256(answer.encode(), usedforsecurity=False).hexdigest(),
                citation_ids=citation_ids,
                citation_gate_passed=True,
                expected_evidence_covered=True,
            )
            self.llm_checkpoints.save("uat_llm", candidate_id, checkpoint)
            completed += 1
        return {
            "candidate_count": 78,
            "request_count": int(str(manifest["request_count"])),
            "completed_count": completed,
            "citation_gate_passed_count": completed,
            "automatic_retries": 0,
            "user_result_review_required": True,
            "real_uat_passed": False,
            "content_output": False,
            "secret_values_in_output": False,
        }


class UatCombinedLlmExecutionRunner:
    revision = "uat-combined-llm-runner:v2"
    namespace = "uat_llm_v2"
    gate_revision = "uat-combined-reranker-gate:v3"
    required_sources = frozenset({"v1", "v2", "v3"})
    result_revision = "real-uat-result:v2"
    _allowed_statuses = UatLlmExecutionRunner._allowed_statuses

    def __init__(
        self,
        transport: UatLlmTransportPort,
        checkpoints: CheckpointStorePort,
        result_store: UatResultStorePort,
        *,
        external_call_approved: bool,
        max_requests: int = 78,
        reranker_top_k: int = 2,
        timeout_seconds: float = 120,
    ) -> None:
        self.transport = transport
        self.checkpoints = checkpoints
        self.result_store = result_store
        self.external_call_approved = external_call_approved
        self.max_requests = max_requests
        self.reranker_top_k = reranker_top_k
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        bundles: Sequence[Mapping[str, object]],
        combined_gate: Mapping[str, object],
    ) -> dict[str, object]:
        if self.transport.real_network and not self.external_call_approved:
            raise ExecutionApprovalRequired("UAT_COMBINED_LLM_EXECUTION_APPROVAL_REQUIRED")
        if self.max_requests != 78 or self.reranker_top_k != 2:
            raise ProviderExecutionError("UAT_COMBINED_LLM_EXECUTION_PARAMETERS_INVALID")
        items = _validated_bundles(bundles)
        bindings = [_bundle_binding(bundle) for bundle in items]
        binding_by_candidate = {str(binding["candidate_id"]): binding for binding in bindings}
        raw_results = combined_gate.get("results")
        source_hashes = combined_gate.get("source_checkpoint_hashes")
        if (
            combined_gate.get("revision") != self.gate_revision
            or combined_gate.get("candidate_count") != 78
            or combined_gate.get("gate_passed_count") != 78
            or combined_gate.get("positive_top_k") != 2
            or combined_gate.get("bundle_snapshot_hash") != _canonical_hash(bindings)
            or combined_gate.get("llm_execution_unlocked") is not True
            or not isinstance(raw_results, Sequence)
            or isinstance(raw_results, (str, bytes))
            or len(raw_results) != 78
            or not isinstance(source_hashes, Mapping)
            or set(source_hashes) != self.required_sources
        ):
            raise ProviderExecutionError("UAT_COMBINED_LLM_GLOBAL_GATE_NOT_MET")
        result_by_candidate: dict[str, dict[str, object]] = {}
        for bundle, raw_result in zip(items, raw_results, strict=True):
            candidate_id = str(bundle["candidate_id"])
            documents = _bundle_documents(bundle)
            evidence_ids = [str(document["evidence_id"]) for document in documents]
            if not isinstance(raw_result, Mapping):
                raise ProviderExecutionError("UAT_COMBINED_LLM_RERANKER_RESULT_INVALID")
            ranked_ids = raw_result.get("ranked_evidence_ids")
            positive_rank = raw_result.get("positive_rank")
            if (
                raw_result.get("candidate_id") != candidate_id
                or raw_result.get("gate_passed") is not True
                or raw_result.get("bundle_sha256")
                != binding_by_candidate[candidate_id]["bundle_sha256"]
                or raw_result.get("question_sha256")
                != binding_by_candidate[candidate_id]["question_sha256"]
                or raw_result.get("source_checkpoint") not in self.required_sources
                or not isinstance(ranked_ids, Sequence)
                or isinstance(ranked_ids, (str, bytes))
                or len(ranked_ids) != 4
                or any(not isinstance(value, str) for value in ranked_ids)
                or len(set(ranked_ids)) != 4
                or set(ranked_ids) != set(evidence_ids)
                or not isinstance(positive_rank, int)
                or isinstance(positive_rank, bool)
                or positive_rank
                != list(ranked_ids).index(str(bundle["expected_positive_evidence_id"])) + 1
                or positive_rank > self.reranker_top_k
            ):
                raise ProviderExecutionError("UAT_COMBINED_LLM_RERANKER_RESULT_INVALID")
            result_by_candidate[candidate_id] = dict(raw_result)
        combined_gate_hash = _canonical_hash(combined_gate)
        snapshot_hash = _canonical_hash(
            [
                {
                    "bundle": binding_by_candidate[str(bundle["candidate_id"])],
                    "reranker": result_by_candidate[str(bundle["candidate_id"])],
                }
                for bundle in items
            ]
        )
        manifest_parameters = {
            "revision": self.revision,
            "snapshot_hash": snapshot_hash,
            "combined_gate_hash": combined_gate_hash,
            "candidate_count": 78,
            "max_requests": 78,
            "reranker_top_k": 2,
            "automatic_retries": 0,
            "user_result_review_required": True,
        }
        manifest = self.checkpoints.get(self.namespace, "_manifest")
        if manifest is None:
            manifest = {**manifest_parameters, "request_count": 0}
            self.checkpoints.save(self.namespace, "_manifest", manifest)
        else:
            _validate_manifest_parameters(
                manifest,
                manifest_parameters,
                "UAT_COMBINED_LLM_SNAPSHOT_OR_PARAMETERS_MISMATCH",
            )
        completed = 0
        for bundle in items:
            candidate_id = str(bundle["candidate_id"])
            reranker_result = result_by_candidate[candidate_id]
            ranked_ids = reranker_result["ranked_evidence_ids"]
            assert isinstance(ranked_ids, list)
            selected_ids = ranked_ids[: self.reranker_top_k]
            positive_id = str(bundle["expected_positive_evidence_id"])
            checkpoint = self.checkpoints.get(self.namespace, candidate_id)
            expected_checkpoint_binding = {
                "candidate_id": candidate_id,
                "selected_evidence_ids": selected_ids,
                "expected_positive_evidence_id": positive_id,
                "question_sha256": binding_by_candidate[candidate_id]["question_sha256"],
                "bundle_sha256": binding_by_candidate[candidate_id]["bundle_sha256"],
                "bundle_revision": binding_by_candidate[candidate_id]["bundle_revision"],
                "combined_gate_hash": combined_gate_hash,
                "reranker_result_sha256": _canonical_hash(reranker_result),
            }
            if checkpoint is not None:
                if any(
                    checkpoint.get(key) != value
                    for key, value in expected_checkpoint_binding.items()
                ):
                    raise ProviderExecutionError("UAT_COMBINED_LLM_CHECKPOINT_BINDING_MISMATCH")
                if checkpoint.get("state") == "COMPLETED":
                    citations = checkpoint.get("citation_ids")
                    if (
                        checkpoint.get("citation_gate_passed") is not True
                        or checkpoint.get("expected_evidence_covered") is not True
                        or not isinstance(citations, Sequence)
                        or isinstance(citations, (str, bytes))
                        or any(value not in selected_ids for value in citations)
                        or positive_id not in citations
                    ):
                        raise ProviderExecutionError("UAT_COMBINED_LLM_CHECKPOINT_RESULT_INVALID")
                    completed += 1
                    continue
                raise ProviderExecutionError(
                    str(
                        checkpoint.get(
                            "error_code", "UAT_COMBINED_LLM_PREVIOUS_ATTEMPT_BLOCKS_RESUME"
                        )
                    )
                )
            if int(str(manifest["request_count"])) >= self.max_requests:
                raise ProviderExecutionError("UAT_COMBINED_LLM_REQUEST_BUDGET_EXCEEDED")
            documents = [dict(document) for document in _bundle_documents(bundle)]
            by_id = {str(document["evidence_id"]): document for document in documents}
            selected = [by_id[evidence_id] for evidence_id in selected_ids]
            checkpoint = {
                "state": "UNKNOWN_OUTCOME",
                **expected_checkpoint_binding,
                "idempotency_key": f"uat-llm-v2-{candidate_id}",
                "automatic_retries": 0,
            }
            manifest["request_count"] = int(str(manifest["request_count"])) + 1
            self.checkpoints.save(self.namespace, "_manifest", manifest)
            self.checkpoints.save(self.namespace, candidate_id, checkpoint)
            try:
                result = self.transport.generate(
                    str(bundle["question"]),
                    [
                        {
                            "evidence_id": item["evidence_id"],
                            "locator": item["locator"],
                            "content": item["content"],
                        }
                        for item in selected
                    ],
                    f"uat-llm-v2-{candidate_id}",
                    self.timeout_seconds,
                )
                status = result.get("status")
                answer = result.get("answer")
                citations = result.get("citation_ids")
                if (
                    status not in self._allowed_statuses
                    or not isinstance(answer, str)
                    or not answer.strip()
                    or not isinstance(citations, Sequence)
                    or isinstance(citations, (str, bytes))
                    or any(not isinstance(value, str) for value in citations)
                ):
                    raise ProviderExecutionError(
                        "UAT_COMBINED_LLM_RESPONSE_SCHEMA_INVALID", outcome_unknown=False
                    )
                citation_ids = [str(value) for value in citations]
                if (
                    len(set(citation_ids)) != len(citation_ids)
                    or any(value not in selected_ids for value in citation_ids)
                    or positive_id not in citation_ids
                ):
                    raise ProviderExecutionError(
                        "UAT_COMBINED_LLM_CITATION_GATE_FAILED", outcome_unknown=False
                    )
            except Exception as error:
                code, failed = _safe_failure(error, checkpoint, "UAT_COMBINED_LLM_FAILURE")
                self.checkpoints.save(self.namespace, candidate_id, failed)
                raise ProviderExecutionError(code) from None
            stored = self.result_store.persist_result(
                candidate_id,
                {
                    "revision": self.result_revision,
                    "candidate_id": candidate_id,
                    "status": status,
                    "answer": answer,
                    "citation_ids": citation_ids,
                    "expected_positive_evidence_id": positive_id,
                    "locator_grounded": True,
                    "user_review_status": "PENDING_USER_RESULT_REVIEW",
                },
            )
            checkpoint.update(
                state="COMPLETED",
                result_ref=stored["result_ref"],
                result_sha256=stored["result_sha256"],
                answer_sha256=hashlib.sha256(answer.encode(), usedforsecurity=False).hexdigest(),
                citation_ids=citation_ids,
                citation_gate_passed=True,
                expected_evidence_covered=True,
            )
            self.checkpoints.save(self.namespace, candidate_id, checkpoint)
            completed += 1
        return {
            "candidate_count": 78,
            "request_count": int(str(manifest["request_count"])),
            "completed_count": completed,
            "citation_gate_passed_count": completed,
            "automatic_retries": 0,
            "user_result_review_required": True,
            "real_uat_passed": False,
            "content_output": False,
            "secret_values_in_output": False,
        }


class UatSystematicLlmV3ExecutionRunner(UatCombinedLlmExecutionRunner):
    revision = "uat-systematic-llm-runner:v3"
    namespace = "uat_llm_v3"
    gate_revision = "uat-combined-reranker-gate:v4"
    required_sources = frozenset({"v1", "v2", "v3", "v4"})
    result_revision = "real-uat-result:v3"


class UatSystematicLlmV4ExecutionRunner(UatCombinedLlmExecutionRunner):
    """Run conditional LLM UAT only after the v5 78/78 combined Gate passes."""

    revision = "uat-systematic-llm-runner:v4"
    namespace = "uat_llm_v4"
    gate_revision = "uat-combined-reranker-gate:v5"
    required_sources = frozenset({"v1", "v2", "v3", "v4", "v5"})
    result_revision = "real-uat-result:v4"
