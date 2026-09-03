"""Ports and safe errors for deferred real-provider execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Protocol


class ProviderExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        status_code: int | None = None,
        provider_error_code: str | None = None,
        provider_error_type: str | None = None,
        trace_id_hash: str | None = None,
        outcome_unknown: bool | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.provider_error_code = provider_error_code
        self.provider_error_type = provider_error_type
        self.trace_id_hash = trace_id_hash
        self.outcome_unknown = outcome_unknown


class ExecutionApprovalRequired(ProviderExecutionError):
    pass


class CheckpointStorePort(Protocol):
    def get(self, namespace: str, key: str) -> dict[str, Any] | None: ...

    def save(self, namespace: str, key: str, value: Mapping[str, Any]) -> None: ...


class ResultStorePort(Protocol):
    def persist_mineru_result(
        self,
        anonymous_id: str,
        result_hash: str,
        zip_payload: bytes,
        nodes: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, object]: ...

    def read_mineru_nodes(self, artifact_id: str) -> Sequence[Mapping[str, Any]]: ...


class OwnedProcessResult(NamedTuple):
    return_code: int
    stdout: bytes
    stderr: bytes


class OwnedProcessRunnerPort(Protocol):
    def run(
        self, command: Sequence[str], *, cwd: Path, timeout_seconds: float
    ) -> OwnedProcessResult: ...


class MinerUTokenLeasePort(Protocol):
    slot: int

    def secret_value(self) -> str: ...

    def release(self) -> None: ...


class MinerUTokenPoolPort(Protocol):
    def acquire(self) -> MinerUTokenLeasePort: ...

    def acquire_slot(self, slot: int) -> MinerUTokenLeasePort: ...

    def record_success(self, slot: int) -> None: ...

    def record_failure(self, slot: int, *, rate_limited: bool = False) -> None: ...


class MinerUTransportPort(Protocol):
    real_network: bool

    def create_batch(
        self,
        token: str,
        anonymous_name: str,
        data_id: str,
        is_ocr: bool,
        page_ranges: str | None,
        model_version: str,
        enable_table: bool,
        enable_formula: bool,
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...

    def put_signed(self, file_url: str, source: Path, timeout_seconds: float) -> None: ...

    def batch_status(
        self, token: str, batch_id: str, timeout_seconds: float
    ) -> Mapping[str, Any]: ...

    def download_zip(self, full_zip_url: str, timeout_seconds: float) -> bytes: ...


class EmbeddingBatchTransportPort(Protocol):
    real_network: bool

    def embed(
        self,
        texts: Sequence[str],
        idempotency_key: str,
        timeout_seconds: float,
    ) -> Sequence[Sequence[float]]: ...


class UatRerankerTransportPort(Protocol):
    real_network: bool

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: int,
        idempotency_key: str,
        timeout_seconds: float,
    ) -> Sequence[int]: ...


class UatLlmTransportPort(Protocol):
    real_network: bool

    def generate(
        self,
        question: str,
        evidence: Sequence[Mapping[str, object]],
        idempotency_key: str,
        timeout_seconds: float,
    ) -> Mapping[str, object]: ...


class UatClaimTransportPort(Protocol):
    """Future-only transport for the versioned structured-claim UAT contract."""

    real_network: bool

    def generate_claims(
        self,
        contract: Mapping[str, object],
        idempotency_key: str,
        timeout_seconds: float,
    ) -> Mapping[str, object]: ...


class UatClaimArtifactStorePort(Protocol):
    """Future-only storage port for claim results and content-free audit manifests."""

    def persist_claim_audit_manifest(
        self, test_case_id: str, manifest: Mapping[str, Any]
    ) -> Mapping[str, object]: ...

    def persist_claim_result(
        self, test_case_id: str, result: Mapping[str, Any]
    ) -> Mapping[str, object]: ...

    def read_claim_audit_manifest(self, test_case_id: str) -> Mapping[str, object]: ...

    def persist_claim_coverage_manifest(
        self, manifest: Mapping[str, Any]
    ) -> Mapping[str, object]: ...

    def read_claim_coverage_manifest(self) -> Mapping[str, object] | None: ...


class UatResultStorePort(Protocol):
    def persist_result(
        self, candidate_id: str, result: Mapping[str, Any]
    ) -> Mapping[str, object]: ...
