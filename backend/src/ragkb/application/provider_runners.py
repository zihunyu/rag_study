"""Budgeted, checkpointed real-provider runners with zero automatic retries."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import time
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from ragkb.contracts.provider_execution import (
    CheckpointStorePort,
    EmbeddingBatchTransportPort,
    ExecutionApprovalRequired,
    MinerUTokenPoolPort,
    MinerUTransportPort,
    ProviderExecutionError,
    ResultStorePort,
)


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded, usedforsecurity=False).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


_MINERU_CONTENT_FIELDS = (
    "text",
    "content",
    "table_body",
    "table_caption",
    "table_footnote",
    "list_items",
    "code_body",
    "code_caption",
    "code_footnote",
    "image_caption",
    "image_footnote",
    "chart_caption",
    "chart_footnote",
)


def _content_text(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Mapping):
        parts: list[str] = []
        for nested in value.values():
            parts.extend(_content_text(nested))
        return parts
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        parts = []
        for nested in value:
            parts.extend(_content_text(nested))
        return parts
    return []


def _safe_provider_state(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.casefold()
    if 0 < len(normalized) <= 64 and re.fullmatch(r"[a-z0-9_-]+", normalized):
        return normalized
    return None


def _safe_provider_code(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and re.fullmatch(r"[A-Za-z0-9_.:/-]+", value)
    ):
        return value
    return None


def require_configured_provider_egress(
    *,
    outbound_ai_allowed: bool,
    allowed_classifications: Sequence[str],
    approved_processing_regions: Sequence[str],
    classifications: Sequence[str],
) -> None:
    """Fail before reading source bodies unless config authorizes each classification."""

    if not approved_processing_regions:
        raise ExecutionApprovalRequired("PROVIDER_PROCESSING_REGION_NOT_APPROVED")
    normalized_allowed = {item.casefold() for item in allowed_classifications}
    for classification in classifications:
        normalized = classification.casefold()
        if (
            not outbound_ai_allowed
            or normalized == "restricted"
            or normalized not in normalized_allowed
        ):
            raise ExecutionApprovalRequired("PROVIDER_EGRESS_POLICY_DENIED")


def embedding_provider_contract(
    *,
    base_url: str,
    model: str,
    dimension: int,
    configured_batch_size: int,
    chunk_count: int,
    approved_max_batches: int,
) -> dict[str, object]:
    """Describe provider limits without exposing the configured endpoint."""

    parsed = urlparse(base_url)
    host = (parsed.hostname or "").casefold()
    dashscope_v4 = bool(
        host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}
        and "/compatible-mode/" in parsed.path.casefold()
        and model.casefold() == "text-embedding-v4"
    )
    provider_max_batch_size = 10 if dashscope_v4 else configured_batch_size
    planned_batch_size = min(configured_batch_size, provider_max_batch_size)
    configured_required_batches = math.ceil(chunk_count / configured_batch_size)
    required_new_batches = math.ceil(chunk_count / planned_batch_size)
    issues: list[str] = []
    if dashscope_v4 and configured_batch_size > provider_max_batch_size:
        issues.append("EMBEDDING_BATCH_SIZE_EXCEEDS_DASHSCOPE_TEXT_EMBEDDING_V4_LIMIT")
    if dashscope_v4 and dimension != 1024:
        issues.append("EMBEDDING_DIMENSION_MISMATCH_DASHSCOPE_TEXT_EMBEDDING_V4")
    if configured_required_batches > approved_max_batches:
        issues.append("EMBEDDING_REQUIRED_BATCHES_EXCEED_APPROVED_ATTEMPT_BUDGET")
    return {
        "provider_contract": (
            "DASHSCOPE_OPENAI_COMPATIBLE_TEXT_EMBEDDING_V4"
            if dashscope_v4
            else "GENERIC_OPENAI_COMPATIBLE"
        ),
        "configured_batch_size": configured_batch_size,
        "provider_max_batch_size": provider_max_batch_size,
        "planned_batch_size": planned_batch_size,
        "configured_required_batches": configured_required_batches,
        "required_new_batches": required_new_batches,
        "dimension": dimension,
        "approved_max_batches": approved_max_batches,
        "configuration_issues": issues,
        "configuration_valid": not issues,
        "endpoint_value_output": False,
    }


def require_embedding_provider_contract(contract: Mapping[str, object]) -> None:
    issues = contract.get("configuration_issues")
    if isinstance(issues, Sequence) and not isinstance(issues, (str, bytes)) and issues:
        raise ProviderExecutionError(str(issues[0]))


def mineru_provider_error_category(provider_error_code: str | None) -> str:
    return {
        "A0202": "AUTHENTICATION_OR_TOKEN",
        "A0211": "AUTHENTICATION_OR_TOKEN",
        "-500": "PROVIDER_INTERNAL",
        "-10002": "REQUEST_OR_QUOTA",
        "-60001": "FILE_OR_TASK",
    }.get(provider_error_code or "", "PROVIDER_BUSINESS_ERROR_UNCLASSIFIED")


class MinerUExecutionRunner:
    revision = "mineru-execution-runner:v2"

    def __init__(
        self,
        pool: MinerUTokenPoolPort,
        transport: MinerUTransportPort,
        checkpoints: CheckpointStorePort,
        result_store: ResultStorePort,
        *,
        external_call_approved: bool,
        attempt_revision: str = "mineru-attempt:unspecified",
        scope: str = "unspecified",
        locator_policy: str = "strict_page_bbox",
        max_files: int = 10,
        max_requests: int = 330,
        max_polls_per_file: int = 30,
        poll_interval_seconds: float = 10,
        timeout_seconds: float = 300,
        model_version: str = "vlm",
        enable_table: bool = True,
        enable_formula: bool = True,
        max_zip_bytes: int = 100 * 1024 * 1024,
        max_zip_entries: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        after_result_persist: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        if (
            max_files < 1
            or max_requests < 1
            or max_polls_per_file < 1
            or poll_interval_seconds <= 0
        ):
            raise ValueError("MINERU_BUDGET_INVALID")
        self.pool = pool
        self.transport = transport
        self.checkpoints = checkpoints
        self.result_store = result_store
        self.external_call_approved = external_call_approved
        self.attempt_revision = attempt_revision
        self.scope = scope
        if locator_policy not in {"strict_page_bbox", "office_page_bbox_optional"}:
            raise ValueError("MINERU_LOCATOR_POLICY_INVALID")
        self.locator_policy = locator_policy
        self.max_files = max_files
        self.max_requests = max_requests
        self.max_polls_per_file = max_polls_per_file
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.model_version = model_version
        self.enable_table = enable_table
        self.enable_formula = enable_formula
        self.max_zip_bytes = max_zip_bytes
        self.max_zip_entries = max_zip_entries
        self.clock = clock
        self.sleeper = sleeper
        self.after_result_persist = after_result_persist

    def _guard(self) -> None:
        if self.transport.real_network and not self.external_call_approved:
            raise ExecutionApprovalRequired("MINERU_EXECUTION_APPROVAL_REQUIRED")

    def _manifest(self) -> dict[str, Any]:
        existing = self.checkpoints.get("mineru", "_manifest")
        if existing is not None:
            if (
                existing.get("attempt_revision") != self.attempt_revision
                or existing.get("scope") != self.scope
            ):
                raise ProviderExecutionError("MINERU_ATTEMPT_SCOPE_MISMATCH")
            return existing
        return {
            "attempt_revision": self.attempt_revision,
            "scope": self.scope,
            "files": [],
            "request_count": 0,
            "max_files": self.max_files,
            "max_requests": self.max_requests,
            "automatic_retries": 0,
        }

    def _reserve_request(self, manifest: dict[str, Any]) -> None:
        if int(manifest["request_count"]) >= self.max_requests:
            raise ProviderExecutionError("MINERU_REQUEST_BUDGET_EXCEEDED")
        manifest["request_count"] = int(manifest["request_count"]) + 1
        self.checkpoints.save("mineru", "_manifest", manifest)

    def validate_result_zip(
        self, payload: bytes, anonymous_id: str
    ) -> tuple[list[dict[str, object]], int, str]:
        if not payload or len(payload) > self.max_zip_bytes:
            raise ProviderExecutionError("MINERU_RESULT_ZIP_SIZE_INVALID")
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as error:
            raise ProviderExecutionError("MINERU_RESULT_ZIP_INVALID") from error
        with archive:
            entries = archive.infolist()
            if not entries or len(entries) > self.max_zip_entries:
                raise ProviderExecutionError("MINERU_RESULT_ZIP_ENTRIES_INVALID")
            total_size = 0
            content_entries: list[zipfile.ZipInfo] = []
            for entry in entries:
                path = PurePosixPath(entry.filename.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts:
                    raise ProviderExecutionError("MINERU_RESULT_ZIP_PATH_INVALID")
                total_size += entry.file_size
                if total_size > self.max_zip_bytes:
                    raise ProviderExecutionError("MINERU_RESULT_ZIP_EXPANDED_TOO_LARGE")
                if path.name == "content_list.json" or path.name.endswith("_content_list.json"):
                    content_entries.append(entry)
            if not content_entries:
                raise ProviderExecutionError("MINERU_CONTENT_LIST_MISSING")
            if len(content_entries) != 1:
                raise ProviderExecutionError("MINERU_CONTENT_LIST_AMBIGUOUS")
            try:
                content = json.loads(archive.read(content_entries[0]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ProviderExecutionError("MINERU_CONTENT_LIST_INVALID") from error
        if not isinstance(content, list) or not content:
            raise ProviderExecutionError("MINERU_CONTENT_LIST_INVALID")
        result_hash = hashlib.sha256(payload, usedforsecurity=False).hexdigest()
        nodes: list[dict[str, object]] = []
        for index, item in enumerate(content):
            if not isinstance(item, Mapping):
                raise ProviderExecutionError("MINERU_CONTENT_ITEM_INVALID")
            item_type = str(item.get("type", "")).casefold()
            if item_type not in {
                "text",
                "title",
                "table",
                "image",
                "chart",
                "equation",
                "code",
                "list",
                "index",
                "header",
                "footer",
                "page_number",
                "aside_text",
                "page_footnote",
            }:
                raise ProviderExecutionError("MINERU_CONTENT_TYPE_INVALID")
            page_index = item.get("page_idx")
            bbox = item.get("bbox")
            office_scope = self.locator_policy == "office_page_bbox_optional"
            valid_page = bool(
                isinstance(page_index, int) and not isinstance(page_index, bool) and page_index >= 0
            )
            valid_bbox = bool(
                bbox is not None
                and isinstance(bbox, Sequence)
                and not isinstance(bbox, (str, bytes))
                and len(bbox) == 4
                and all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    for value in bbox
                )
            )
            if (
                not valid_page
                or (not office_scope and not valid_bbox)
                or (bbox is not None and not valid_bbox)
            ):
                raise ProviderExecutionError("MINERU_CONTENT_LOCATOR_INVALID")
            assert isinstance(page_index, int)
            structured_content = {
                field: item[field] for field in _MINERU_CONTENT_FIELDS if field in item
            }
            text_parts: list[str] = []
            for value in structured_content.values():
                text_parts.extend(_content_text(value))
            display_text = "\n".join(dict.fromkeys(text_parts))
            locator: dict[str, object] = {"page": page_index + 1}
            if valid_bbox:
                assert isinstance(bbox, Sequence)
                locator["bbox"] = [float(value) for value in bbox]
            node_id = hashlib.sha256(
                (
                    f"{anonymous_id}:{result_hash}:{index}:{item_type}:"
                    f"{json.dumps(locator, sort_keys=True, separators=(',', ':'))}"
                ).encode(),
                usedforsecurity=False,
            ).hexdigest()[:32]
            nodes.append(
                {
                    "node_id": node_id,
                    "anonymous_sample_id": anonymous_id,
                    "type": item_type,
                    "original_text": display_text,
                    "display_text": display_text,
                    "content": structured_content,
                    "locator": locator,
                }
            )
        return nodes, len(nodes), result_hash

    def run_file(
        self,
        source: Path,
        anonymous_id: str,
        expected_sha256: str,
        *,
        is_ocr: bool = True,
    ) -> dict[str, object]:
        self._guard()
        if _file_hash(source) != expected_sha256.casefold():
            raise ProviderExecutionError("MINERU_INPUT_SNAPSHOT_MISMATCH")
        checkpoint = self.checkpoints.get("mineru", anonymous_id)
        if checkpoint and checkpoint.get("state") == "COMPLETED":
            return dict(checkpoint["evidence"])
        if checkpoint and (
            str(checkpoint.get("state", "")).endswith("_IN_FLIGHT")
            or checkpoint.get("state") == "UNKNOWN_OUTCOME"
        ):
            raise ProviderExecutionError("MINERU_UNKNOWN_OUTCOME_MANUAL_RECONCILIATION_REQUIRED")
        if checkpoint and checkpoint.get("state") == "UNSUPPORTED_PROVIDER_STATE":
            raise ProviderExecutionError("MINERU_TASK_STATE_UNSUPPORTED")
        if checkpoint and checkpoint.get("state") == "FAILED":
            raise ProviderExecutionError(str(checkpoint.get("error_code", "MINERU_FILE_FAILED")))
        manifest = self._manifest()
        files = list(manifest["files"])
        if anonymous_id not in files:
            if len(files) >= self.max_files:
                raise ProviderExecutionError("MINERU_FILE_BUDGET_EXCEEDED")
            files.append(anonymous_id)
            manifest["files"] = files
            self.checkpoints.save("mineru", "_manifest", manifest)
        if checkpoint and "token_slot" in checkpoint:
            lease = self.pool.acquire_slot(int(checkpoint["token_slot"]))
        else:
            lease = self.pool.acquire()
            checkpoint = {
                "state": "ASSIGNED",
                "token_slot": lease.slot,
                "snapshot_hash": expected_sha256.casefold(),
                "attempt_revision": self.attempt_revision,
                "scope": self.scope,
                "automatic_retries": 0,
            }
            self.checkpoints.save("mineru", anonymous_id, checkpoint)
        deadline = self.clock() + self.timeout_seconds
        try:
            token = lease.secret_value()
            full_zip_url: str | None = None
            if checkpoint["state"] == "ASSIGNED":
                self._reserve_request(manifest)
                checkpoint.update(state="UNKNOWN_OUTCOME", operation="CREATE_BATCH")
                self.checkpoints.save("mineru", anonymous_id, checkpoint)
                created = self.transport.create_batch(
                    token,
                    f"sample-{anonymous_id}{source.suffix.casefold()}",
                    anonymous_id,
                    is_ocr,
                    None,
                    self.model_version,
                    self.enable_table,
                    self.enable_formula,
                    self.timeout_seconds,
                )
                batch_id = created.get("batch_id")
                file_url = created.get("file_url")
                if (
                    not isinstance(batch_id, str)
                    or not batch_id
                    or not isinstance(file_url, str)
                    or not file_url
                ):
                    raise ProviderExecutionError("MINERU_CREATE_BATCH_SCHEMA_INVALID")
                self._reserve_request(manifest)
                checkpoint.update(
                    state="UNKNOWN_OUTCOME",
                    operation="SIGNED_PUT",
                    batch_id=batch_id,
                )
                self.checkpoints.save("mineru", anonymous_id, checkpoint)
                self.transport.put_signed(file_url, source, self.timeout_seconds)
                checkpoint.update(state="SUBMITTED", poll_count=0)
                checkpoint.pop("operation", None)
                self.checkpoints.save("mineru", anonymous_id, checkpoint)
            while checkpoint["state"] == "SUBMITTED":
                if self.clock() >= deadline:
                    raise ProviderExecutionError("MINERU_TIMEOUT")
                if int(checkpoint["poll_count"]) >= self.max_polls_per_file:
                    raise ProviderExecutionError("MINERU_POLL_BUDGET_EXCEEDED")
                self._reserve_request(manifest)
                status = self.transport.batch_status(
                    token, str(checkpoint["batch_id"]), self.timeout_seconds
                )
                checkpoint["poll_count"] = int(checkpoint["poll_count"]) + 1
                results = status.get("extract_result")
                if not isinstance(results, Sequence) or len(results) != 1:
                    raise ProviderExecutionError("MINERU_BATCH_STATUS_SCHEMA_INVALID")
                item = results[0]
                if not isinstance(item, Mapping):
                    raise ProviderExecutionError("MINERU_BATCH_STATUS_SCHEMA_INVALID")
                if item.get("data_id") not in {None, anonymous_id}:
                    raise ProviderExecutionError("MINERU_BATCH_DATA_ID_MISMATCH")
                state = _safe_provider_state(item.get("state", item.get("status")))
                if state is None:
                    checkpoint["provider_state"] = "UNSAFE_OR_MISSING"
                    raise ProviderExecutionError(
                        "MINERU_TASK_STATE_UNSUPPORTED", outcome_unknown=False
                    )
                checkpoint["provider_state"] = state
                if state in {
                    "waiting-file",
                    "uploading",
                    "pending",
                    "running",
                    "processing",
                    "converting",
                }:
                    self.checkpoints.save("mineru", anonymous_id, checkpoint)
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise ProviderExecutionError("MINERU_TIMEOUT")
                    self.sleeper(min(self.poll_interval_seconds, remaining))
                    continue
                if state in {"failed", "error", "canceled", "cancelled"}:
                    raise ProviderExecutionError(
                        "MINERU_TASK_EXPLICIT_FAILED",
                        provider_error_code=_safe_provider_code(
                            item.get("err_code", item.get("error_code"))
                        ),
                        outcome_unknown=False,
                    )
                if state not in {"done", "completed", "success"}:
                    raise ProviderExecutionError(
                        "MINERU_TASK_STATE_UNSUPPORTED", outcome_unknown=False
                    )
                raw_zip_url = item.get("full_zip_url")
                if not isinstance(raw_zip_url, str) or not raw_zip_url:
                    raise ProviderExecutionError("MINERU_STATUS_SCHEMA_INVALID")
                full_zip_url = raw_zip_url
                self._reserve_request(manifest)
                checkpoint.update(state="UNKNOWN_OUTCOME", operation="DOWNLOAD_RESULT")
                self.checkpoints.save("mineru", anonymous_id, checkpoint)
                result_zip = self.transport.download_zip(full_zip_url, self.timeout_seconds)
                checkpoint["state"] = "RESULT_RECEIVED"
                checkpoint.pop("operation", None)
                nodes, locator_count, result_hash = self.validate_result_zip(
                    result_zip, anonymous_id
                )
                checkpoint.update(
                    state="UNKNOWN_OUTCOME",
                    operation="PERSIST_RESULT",
                    result_hash=result_hash,
                )
                self.checkpoints.save("mineru", anonymous_id, checkpoint)
                artifact = self.result_store.persist_mineru_result(
                    anonymous_id,
                    result_hash,
                    result_zip,
                    nodes,
                )
                artifact_id = artifact.get("artifact_id")
                artifact_ref = artifact.get("artifact_ref")
                artifact_path = (
                    PurePosixPath(artifact_ref)
                    if isinstance(artifact_ref, str)
                    else PurePosixPath(".")
                )
                if (
                    not isinstance(artifact_id, str)
                    or len(artifact_id) != 32
                    or any(
                        character not in "0123456789abcdef" for character in artifact_id.casefold()
                    )
                    or not isinstance(artifact_ref, str)
                    or artifact_path.is_absolute()
                    or ".." in artifact_path.parts
                    or artifact_ref != f"provider-results/mineru/{artifact_id}"
                    or artifact.get("zip_sha256") != result_hash
                    or not isinstance(artifact.get("zip_bytes"), int)
                    or not isinstance(artifact.get("nodes_sha256"), str)
                    or len(str(artifact.get("nodes_sha256"))) != 64
                    or artifact.get("node_count") != len(nodes)
                    or not isinstance(artifact.get("chunk_count"), int)
                ):
                    raise ProviderExecutionError("MINERU_RESULT_ARTIFACT_INVALID")
                if self.after_result_persist is not None:
                    self.after_result_persist(artifact)
                checkpoint.pop("operation", None)
                evidence = {
                    "sample_id": anonymous_id,
                    "attempt_revision": self.attempt_revision,
                    "scope": self.scope,
                    "state": "COMPLETED",
                    "node_count": len(nodes),
                    "locator_count": locator_count,
                    "result_hash": result_hash,
                    "artifact_id": artifact_id,
                    "artifact_ref": artifact_ref,
                    "artifact_bytes": artifact["zip_bytes"],
                    "nodes_hash": artifact["nodes_sha256"],
                    "chunk_count": artifact["chunk_count"],
                    "token_slot": lease.slot,
                    "automatic_retries": 0,
                    "secret_values_in_output": False,
                }
                checkpoint.update(state="COMPLETED", evidence=evidence)
                checkpoint.pop("batch_id", None)
                self.checkpoints.save("mineru", anonymous_id, checkpoint)
                self.pool.record_success(lease.slot)
                return evidence
            raise ProviderExecutionError("MINERU_CHECKPOINT_STATE_INVALID")
        except Exception as error:
            if isinstance(error, ProviderExecutionError):
                code = error.code
            elif checkpoint.get("operation") == "PERSIST_RESULT":
                code = "MINERU_RESULT_PERSIST_FAILURE"
            elif isinstance(error, TimeoutError):
                code = "MINERU_TIMEOUT"
            else:
                code = "MINERU_TRANSPORT_FAILURE"
            self.pool.record_failure(
                lease.slot,
                rate_limited=isinstance(error, ProviderExecutionError) and error.status_code == 429,
            )
            failed = dict(checkpoint)
            if isinstance(error, ProviderExecutionError) and error.outcome_unknown is not None:
                unknown_outcome = error.outcome_unknown
            else:
                unknown_outcome = bool(
                    str(checkpoint.get("state", "")).endswith("_IN_FLIGHT")
                    or checkpoint.get("state") == "UNKNOWN_OUTCOME"
                )
            persistent_state = (
                "UNSUPPORTED_PROVIDER_STATE"
                if code == "MINERU_TASK_STATE_UNSUPPORTED"
                else "UNKNOWN_OUTCOME"
                if unknown_outcome
                else "FAILED"
            )
            failed.update(
                state=persistent_state,
                error_code=code,
                automatic_retries=0,
            )
            if isinstance(error, ProviderExecutionError) and error.status_code is not None:
                failed["http_status"] = error.status_code
            if isinstance(error, ProviderExecutionError) and error.provider_error_code:
                failed["provider_error_code"] = error.provider_error_code
                failed["provider_error_category"] = mineru_provider_error_category(
                    error.provider_error_code
                )
            if isinstance(error, ProviderExecutionError) and error.provider_error_type:
                failed["provider_error_type"] = error.provider_error_type
            if isinstance(error, ProviderExecutionError) and error.trace_id_hash:
                failed["trace_id_hash"] = error.trace_id_hash
            failed.pop("file_url", None)
            failed.pop("full_zip_url", None)
            self.checkpoints.save("mineru", anonymous_id, failed)
            status_code = error.status_code if isinstance(error, ProviderExecutionError) else None
            raise ProviderExecutionError(
                code,
                status_code=status_code,
                provider_error_code=(
                    error.provider_error_code if isinstance(error, ProviderExecutionError) else None
                ),
                provider_error_type=(
                    error.provider_error_type if isinstance(error, ProviderExecutionError) else None
                ),
                trace_id_hash=(
                    error.trace_id_hash if isinstance(error, ProviderExecutionError) else None
                ),
                outcome_unknown=unknown_outcome,
            ) from None
        finally:
            lease.release()


class MinerUDocxRecoveryRunner:
    revision = "mineru-docx-recovery-runner:v1"

    def __init__(
        self,
        pool: MinerUTokenPoolPort,
        transport: MinerUTransportPort,
        checkpoints: CheckpointStorePort,
        result_store: ResultStorePort,
        validator: Callable[[bytes, str], tuple[list[dict[str, object]], int, str]],
        *,
        external_call_approved: bool,
        max_requests: int = 31,
        max_polls: int = 30,
        poll_interval_seconds: float = 10,
        timeout_seconds: float = 300,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.pool = pool
        self.transport = transport
        self.checkpoints = checkpoints
        self.result_store = result_store
        self.validator = validator
        self.external_call_approved = external_call_approved
        self.max_requests = max_requests
        self.max_polls = max_polls
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.sleeper = sleeper

    def run(self, original_failed: Mapping[str, object], anonymous_id: str) -> dict[str, object]:
        if self.transport.real_network and not self.external_call_approved:
            raise ExecutionApprovalRequired("MINERU_DOCX_RECOVERY_APPROVAL_REQUIRED")
        if (
            original_failed.get("state") != "FAILED"
            or original_failed.get("error_code") != "MINERU_CONTENT_LOCATOR_INVALID"
            or not isinstance(original_failed.get("batch_id"), str)
            or not original_failed.get("batch_id")
            or not isinstance(original_failed.get("token_slot"), int)
            or int(str(original_failed.get("poll_count", 0))) < 1
        ):
            raise ProviderExecutionError("MINERU_DOCX_RECOVERY_SOURCE_INVALID")
        existing = self.checkpoints.get("mineru_recovery", anonymous_id)
        if existing is not None:
            if existing.get("state") == "COMPLETED":
                return dict(existing["evidence"])
            raise ProviderExecutionError("MINERU_DOCX_RECOVERY_ALREADY_ATTEMPTED")
        lease = self.pool.acquire_slot(int(str(original_failed["token_slot"])))
        checkpoint: dict[str, object] = {
            "state": "ASSIGNED",
            "attempt_revision": "mineru-docx-recovery:v1",
            "scope": "docx",
            "anonymous_sample_id": anonymous_id,
            "batch_id": str(original_failed["batch_id"]),
            "token_slot": lease.slot,
            "request_count": 0,
            "poll_count": 0,
            "create_count": 0,
            "upload_count": 0,
            "automatic_retries": 0,
        }
        self.checkpoints.save("mineru_recovery", anonymous_id, checkpoint)
        deadline = self.clock() + self.timeout_seconds
        try:
            token = lease.secret_value()
            full_zip_url: str | None = None
            while full_zip_url is None:
                if self.clock() >= deadline:
                    raise ProviderExecutionError(
                        "MINERU_DOCX_RECOVERY_TIMEOUT", outcome_unknown=True
                    )
                if int(str(checkpoint["poll_count"])) >= self.max_polls:
                    raise ProviderExecutionError("MINERU_DOCX_RECOVERY_POLL_BUDGET_EXCEEDED")
                if int(str(checkpoint["request_count"])) >= self.max_requests:
                    raise ProviderExecutionError("MINERU_DOCX_RECOVERY_REQUEST_BUDGET_EXCEEDED")
                checkpoint.update(state="UNKNOWN_OUTCOME", operation="STATUS_QUERY")
                checkpoint["request_count"] = int(str(checkpoint["request_count"])) + 1
                self.checkpoints.save("mineru_recovery", anonymous_id, checkpoint)
                status = self.transport.batch_status(
                    token, str(checkpoint["batch_id"]), self.timeout_seconds
                )
                checkpoint["poll_count"] = int(str(checkpoint["poll_count"])) + 1
                results = status.get("extract_result")
                if not isinstance(results, Sequence) or len(results) != 1:
                    raise ProviderExecutionError("MINERU_DOCX_RECOVERY_STATUS_SCHEMA_INVALID")
                item = results[0]
                if not isinstance(item, Mapping) or item.get("data_id") not in {
                    None,
                    anonymous_id,
                }:
                    raise ProviderExecutionError("MINERU_DOCX_RECOVERY_STATUS_SCHEMA_INVALID")
                state = _safe_provider_state(item.get("state", item.get("status")))
                if state is None:
                    checkpoint["provider_state"] = "UNSAFE_OR_MISSING"
                    raise ProviderExecutionError(
                        "MINERU_TASK_STATE_UNSUPPORTED", outcome_unknown=False
                    )
                checkpoint["provider_state"] = state
                if state in {
                    "waiting-file",
                    "uploading",
                    "pending",
                    "running",
                    "processing",
                    "converting",
                }:
                    checkpoint.update(state="SUBMITTED")
                    checkpoint.pop("operation", None)
                    self.checkpoints.save("mineru_recovery", anonymous_id, checkpoint)
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise ProviderExecutionError(
                            "MINERU_DOCX_RECOVERY_TIMEOUT", outcome_unknown=True
                        )
                    self.sleeper(min(self.poll_interval_seconds, remaining))
                    continue
                if state in {"failed", "error", "canceled", "cancelled"}:
                    raise ProviderExecutionError(
                        "MINERU_TASK_EXPLICIT_FAILED",
                        provider_error_code=_safe_provider_code(
                            item.get("err_code", item.get("error_code"))
                        ),
                        outcome_unknown=False,
                    )
                if state not in {"done", "completed", "success"}:
                    raise ProviderExecutionError(
                        "MINERU_TASK_STATE_UNSUPPORTED", outcome_unknown=False
                    )
                raw_zip_url = item.get("full_zip_url")
                if not isinstance(raw_zip_url, str) or not raw_zip_url:
                    raise ProviderExecutionError("MINERU_DOCX_RECOVERY_STATUS_SCHEMA_INVALID")
                full_zip_url = raw_zip_url
            if int(str(checkpoint["request_count"])) >= self.max_requests:
                raise ProviderExecutionError("MINERU_DOCX_RECOVERY_REQUEST_BUDGET_EXCEEDED")
            checkpoint.update(state="UNKNOWN_OUTCOME", operation="DOWNLOAD_RESULT")
            checkpoint["request_count"] = int(str(checkpoint["request_count"])) + 1
            self.checkpoints.save("mineru_recovery", anonymous_id, checkpoint)
            result_zip = self.transport.download_zip(full_zip_url, self.timeout_seconds)
            checkpoint.update(state="RESULT_RECEIVED")
            checkpoint.pop("operation", None)
            nodes, locator_count, result_hash = self.validator(result_zip, anonymous_id)
            checkpoint.update(
                state="UNKNOWN_OUTCOME",
                operation="PERSIST_RESULT",
                result_hash=result_hash,
            )
            self.checkpoints.save("mineru_recovery", anonymous_id, checkpoint)
            artifact = self.result_store.persist_mineru_result(
                anonymous_id, result_hash, result_zip, nodes
            )
            evidence = {
                "sample_id": anonymous_id,
                "attempt_revision": "mineru-docx-recovery:v1",
                "scope": "docx",
                "state": "COMPLETED",
                "request_count": checkpoint["request_count"],
                "create_count": 0,
                "upload_count": 0,
                "poll_count": checkpoint["poll_count"],
                "download_count": 1,
                "node_count": len(nodes),
                "locator_count": locator_count,
                "chunk_count": artifact["chunk_count"],
                "result_hash": result_hash,
                "artifact_id": artifact["artifact_id"],
                "artifact_ref": artifact["artifact_ref"],
                "automatic_retries": 0,
                "secret_values_in_output": False,
            }
            checkpoint.update(state="COMPLETED", evidence=evidence)
            checkpoint.pop("operation", None)
            checkpoint.pop("batch_id", None)
            self.checkpoints.save("mineru_recovery", anonymous_id, checkpoint)
            self.pool.record_success(lease.slot)
            return evidence
        except Exception as error:
            code = (
                error.code
                if isinstance(error, ProviderExecutionError)
                else "MINERU_DOCX_RECOVERY_FAILURE"
            )
            if isinstance(error, ProviderExecutionError) and error.outcome_unknown is not None:
                unknown = error.outcome_unknown
            else:
                unknown = checkpoint.get("state") == "UNKNOWN_OUTCOME"
            persistent_state = (
                "UNSUPPORTED_PROVIDER_STATE"
                if code == "MINERU_TASK_STATE_UNSUPPORTED"
                else "UNKNOWN_OUTCOME"
                if unknown
                else "FAILED"
            )
            checkpoint.update(
                state=persistent_state,
                error_code=code,
                automatic_retries=0,
            )
            if isinstance(error, ProviderExecutionError) and error.status_code is not None:
                checkpoint["http_status"] = error.status_code
            if isinstance(error, ProviderExecutionError) and error.provider_error_code:
                checkpoint["provider_error_code"] = error.provider_error_code
            if isinstance(error, ProviderExecutionError) and error.provider_error_type:
                checkpoint["provider_error_type"] = error.provider_error_type
            if isinstance(error, ProviderExecutionError) and error.trace_id_hash:
                checkpoint["trace_id_hash"] = error.trace_id_hash
            self.checkpoints.save("mineru_recovery", anonymous_id, checkpoint)
            self.pool.record_failure(
                lease.slot,
                rate_limited=isinstance(error, ProviderExecutionError) and error.status_code == 429,
            )
            raise ProviderExecutionError(code) from None
        finally:
            lease.release()


class MinerUCapabilityProbe:
    revision = "mineru-capability-probe:v1"

    def __init__(self, configured_base_url: str, *, custom_deployment: bool = False) -> None:
        self.configured_base_url = configured_base_url
        self.custom_deployment = custom_deployment

    def probe(self) -> dict[str, object]:
        parsed = urlparse(self.configured_base_url)
        standard = bool(
            not self.custom_deployment
            and parsed.scheme.casefold() == "https"
            and (parsed.hostname or "").casefold() == "mineru.net"
            and parsed.path.rstrip("/") == "/api/v4"
            and not parsed.query
            and not parsed.fragment
            and parsed.username is None
            and parsed.password is None
            and parsed.netloc.casefold() in {"mineru.net", "mineru.net:443"}
        )
        if standard:
            return {
                "status": "DOCX_DOCUMENTED_SUPPORTED",
                "supported_extensions": [
                    "pdf",
                    "doc",
                    "png",
                    "jpg",
                    "jpeg",
                    "docx",
                    "ppt",
                    "pptx",
                    "xlsx",
                    "html",
                ],
                "api_version": "v4",
                "docx_supported": True,
                "request_count": 0,
                "file_content_sent": False,
                "real_docx_execution_approved": False,
                "secret_values_in_output": False,
            }
        return {
            "status": "CAPABILITY_UNCONFIRMED",
            "supported_extensions": [],
            "api_version": "UNKNOWN",
            "docx_supported": False,
            "request_count": 0,
            "file_content_sent": False,
            "real_docx_execution_approved": False,
            "secret_values_in_output": False,
        }


@dataclass(frozen=True)
class EmbeddingChunk:
    chunk_id: str
    text: str


class EmbeddingExecutionRunner:
    revision = "embedding-execution-runner:v2"

    def __init__(
        self,
        transport: EmbeddingBatchTransportPort,
        checkpoints: CheckpointStorePort,
        *,
        dimension: int,
        external_call_approved: bool,
        batch_size: int = 32,
        max_chunks: int = 669,
        max_batches: int,
        timeout_seconds: float = 120,
        after_checkpoint: Callable[[int], None] | None = None,
    ) -> None:
        self.transport = transport
        self.checkpoints = checkpoints
        self.dimension = dimension
        self.external_call_approved = external_call_approved
        self.batch_size = batch_size
        self.max_chunks = max_chunks
        self.max_batches = max_batches
        self.timeout_seconds = timeout_seconds
        self.after_checkpoint = after_checkpoint

    def _guard(self) -> None:
        if self.transport.real_network and not self.external_call_approved:
            raise ExecutionApprovalRequired("EMBEDDING_EXECUTION_APPROVAL_REQUIRED")

    def run(self, chunks: Sequence[EmbeddingChunk]) -> dict[str, object]:
        self._guard()
        if not chunks or len(chunks) > self.max_chunks:
            raise ProviderExecutionError("EMBEDDING_CHUNK_BUDGET_EXCEEDED")
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ProviderExecutionError("EMBEDDING_CHUNK_ID_DUPLICATE")
        batch_count = math.ceil(len(chunks) / self.batch_size)
        if batch_count > self.max_batches:
            raise ProviderExecutionError("EMBEDDING_BATCH_BUDGET_EXCEEDED")
        snapshot = [
            {
                "chunk_id": item.chunk_id,
                "text_hash": hashlib.sha256(item.text.encode(), usedforsecurity=False).hexdigest(),
            }
            for item in chunks
        ]
        snapshot_hash = _canonical_hash(snapshot)
        manifest = self.checkpoints.get("embedding", "_manifest")
        if manifest and manifest.get("snapshot_hash") != snapshot_hash:
            raise ProviderExecutionError("EMBEDDING_SNAPSHOT_MISMATCH")
        if manifest is None:
            manifest = {
                "snapshot_hash": snapshot_hash,
                "chunk_count": len(chunks),
                "batch_count": batch_count,
                "batch_size": self.batch_size,
                "max_batches": self.max_batches,
                "automatic_retries": 0,
            }
            self.checkpoints.save("embedding", "_manifest", manifest)
        vector_hashes: list[str] = []
        completed = 0
        for batch_index in range(batch_count):
            start = batch_index * self.batch_size
            batch = chunks[start : start + self.batch_size]
            batch_key = f"{snapshot_hash[:20]}-{batch_index:03d}"
            checkpoint = self.checkpoints.get("embedding", batch_key)
            if checkpoint and checkpoint.get("state") == "COMPLETED":
                vector_hashes.extend(map(str, checkpoint["vector_hashes"]))
                completed += 1
                continue
            if checkpoint and checkpoint.get("state") in {
                "IN_FLIGHT",
                "UNKNOWN_OUTCOME",
            }:
                raise ProviderExecutionError(
                    "EMBEDDING_UNKNOWN_OUTCOME_MANUAL_RECONCILIATION_REQUIRED"
                )
            if checkpoint and checkpoint.get("state") == "FAILED":
                raise ProviderExecutionError(
                    str(checkpoint.get("error_code", "EMBEDDING_BATCH_FAILED"))
                )
            self.checkpoints.save(
                "embedding",
                batch_key,
                {
                    "state": "UNKNOWN_OUTCOME",
                    "batch_index": batch_index,
                    "idempotency_key": f"embedding-{batch_key}",
                },
            )
            response_received = False
            try:
                vectors = self.transport.embed(
                    [item.text for item in batch],
                    f"embedding-{batch_key}",
                    self.timeout_seconds,
                )
                response_received = True
                if len(vectors) != len(batch):
                    raise ProviderExecutionError("EMBEDDING_RESPONSE_COUNT_MISMATCH")
                hashes: list[str] = []
                stored_vectors: list[list[float]] = []
                for vector in vectors:
                    values = [float(value) for value in vector]
                    if len(values) != self.dimension or not all(
                        math.isfinite(value) for value in values
                    ):
                        raise ProviderExecutionError("EMBEDDING_VECTOR_INVALID")
                    stored_vectors.append(values)
                    hashes.append(_canonical_hash(values))
            except Exception as error:
                code = (
                    error.code
                    if isinstance(error, ProviderExecutionError)
                    else (
                        "EMBEDDING_TIMEOUT"
                        if isinstance(error, TimeoutError)
                        else "EMBEDDING_TRANSPORT_FAILURE"
                    )
                )
                if isinstance(error, ProviderExecutionError) and error.outcome_unknown is not None:
                    unknown_outcome = error.outcome_unknown
                else:
                    unknown_outcome = not response_received
                failed_batch: dict[str, object] = {
                    "state": "UNKNOWN_OUTCOME" if unknown_outcome else "FAILED",
                    "batch_index": batch_index,
                    "chunk_ids": [item.chunk_id for item in batch],
                    "idempotency_key": f"embedding-{batch_key}",
                    "error_code": code,
                    "automatic_retries": 0,
                }
                if isinstance(error, ProviderExecutionError) and error.status_code is not None:
                    failed_batch["http_status"] = error.status_code
                if isinstance(error, ProviderExecutionError) and error.provider_error_code:
                    failed_batch["provider_error_code"] = error.provider_error_code
                if isinstance(error, ProviderExecutionError) and error.provider_error_type:
                    failed_batch["provider_error_type"] = error.provider_error_type
                if isinstance(error, ProviderExecutionError) and error.trace_id_hash:
                    failed_batch["trace_id_hash"] = error.trace_id_hash
                self.checkpoints.save("embedding", batch_key, failed_batch)
                status_code = (
                    error.status_code if isinstance(error, ProviderExecutionError) else None
                )
                raise ProviderExecutionError(
                    code,
                    status_code=status_code,
                    provider_error_code=(
                        error.provider_error_code
                        if isinstance(error, ProviderExecutionError)
                        else None
                    ),
                    provider_error_type=(
                        error.provider_error_type
                        if isinstance(error, ProviderExecutionError)
                        else None
                    ),
                    trace_id_hash=(
                        error.trace_id_hash if isinstance(error, ProviderExecutionError) else None
                    ),
                    outcome_unknown=unknown_outcome,
                ) from None
            self.checkpoints.save(
                "embedding",
                batch_key,
                {
                    "state": "COMPLETED",
                    "batch_index": batch_index,
                    "chunk_ids": [item.chunk_id for item in batch],
                    "vector_hashes": hashes,
                    "vectors": stored_vectors,
                    "automatic_retries": 0,
                },
            )
            vector_hashes.extend(hashes)
            completed += 1
            if self.after_checkpoint is not None:
                self.after_checkpoint(batch_index)
        return {
            "snapshot_hash": snapshot_hash,
            "chunk_count": len(chunks),
            "batch_count": batch_count,
            "completed_batches": completed,
            "vector_hashes": vector_hashes,
            "automatic_retries": 0,
            "zilliz_write_performed": False,
            "secret_values_in_output": False,
        }
