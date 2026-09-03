from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from pathlib import Path

import pytest
from ragkb.adapters.mineru_pool import MinerUTokenPool
from ragkb.application.provider_runners import (
    EmbeddingChunk,
    EmbeddingExecutionRunner,
    MinerUCapabilityProbe,
    MinerUDocxRecoveryRunner,
    MinerUExecutionRunner,
    embedding_provider_contract,
    mineru_provider_error_category,
    require_configured_provider_egress,
    require_embedding_provider_contract,
)
from ragkb.contracts.provider_execution import ExecutionApprovalRequired, ProviderExecutionError
from ragkb.evaluation.local_sample_validation import aggregate_persisted_mineru_evidence
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore
from ragkb.infrastructure.provider_results import LocalProviderResultStore


def _result_zip(*, invalid: bool = False, duplicate_content_list: bool = False) -> bytes:
    if invalid:
        return b"not-a-zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        content = json.dumps(
            [
                {
                    "type": "text",
                    "text": "fake provider text",
                    "page_idx": 0,
                    "bbox": [0, 0, 10, 10],
                },
                {
                    "type": "table",
                    "table_body": "<table><tr><td>fake cell</td></tr></table>",
                    "page_idx": 1,
                    "bbox": [0, 0, 10, 10],
                },
                {
                    "type": "list",
                    "list_items": ["fake item one", "fake item two"],
                    "page_idx": 2,
                    "bbox": [0, 0, 10, 10],
                },
                {
                    "type": "code",
                    "code_body": "fake_code()",
                    "page_idx": 3,
                    "bbox": [0, 0, 10, 10],
                },
                {
                    "type": "header",
                    "text": "fake header",
                    "page_idx": 4,
                    "bbox": [0, 0, 10, 10],
                },
            ]
        )
        archive.writestr(
            "sample-anonymous_content_list.json",
            content,
        )
        if duplicate_content_list:
            archive.writestr(
                "other_content_list.json",
                content,
            )
        archive.writestr("full.md", "fake markdown")
    return buffer.getvalue()


def _office_result_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "anonymous_content_list.json",
            json.dumps(
                [
                    {"type": "text", "text": "office page one", "page_idx": 0},
                    {"type": "index", "list_items": ["office index"], "page_idx": 1},
                ]
            ),
        )
    return buffer.getvalue()


class _MinerUTransport:
    real_network = False

    def __init__(
        self,
        *,
        failure: str | None = None,
        invalid_result: bool = False,
        duplicate_content_list: bool = False,
        pending_polls: int = 0,
    ) -> None:
        self.failure = failure
        self.invalid_result = invalid_result
        self.duplicate_content_list = duplicate_content_list
        self.pending_polls = pending_polls
        self.calls: list[tuple[str, str]] = []
        self.data_id = ""

    def create_batch(
        self,
        token,
        anonymous_name,
        data_id,
        is_ocr,
        page_ranges,
        model_version,
        enable_table,
        enable_formula,
        timeout_seconds,
    ):
        del (
            anonymous_name,
            is_ocr,
            page_ranges,
            model_version,
            enable_table,
            enable_formula,
            timeout_seconds,
        )
        self.data_id = data_id
        self.calls.append(("create_batch", token))
        if self.failure == "create_crash":
            raise SystemExit("injected hard interruption")
        if self.failure == "429":
            raise ProviderExecutionError(
                "RATE_LIMIT",
                status_code=429,
                provider_error_code="A0202",
                trace_id_hash="a" * 64,
                outcome_unknown=False,
            )
        if self.failure == "timeout":
            raise TimeoutError("safe fake transport failure")
        if self.failure == "500":
            raise ProviderExecutionError(
                "MINERU_CREATE_BATCH_SERVER_ERROR",
                status_code=500,
                outcome_unknown=True,
            )
        return {
            "batch_id": "batch-opaque",
            "file_url": "https://upload.example/signed?signature=temporary",
        }

    def put_signed(self, file_url, source, timeout_seconds):
        del file_url, source, timeout_seconds
        self.calls.append(("put_signed", "NO_AUTH"))
        if self.failure == "put_crash":
            raise SystemExit("injected hard interruption")
        if self.failure == "put_timeout":
            raise TimeoutError("safe fake signed upload timeout")

    def batch_status(self, token, batch_id, timeout_seconds):
        del batch_id, timeout_seconds
        self.calls.append(("batch_status", token))
        if self.pending_polls > 0:
            self.pending_polls -= 1
            return {"extract_result": [{"data_id": self.data_id, "state": "processing"}]}
        return {
            "batch_id": "batch-opaque",
            "extract_result": [
                {
                    "data_id": self.data_id,
                    "state": "done",
                    "full_zip_url": "https://download.example/result.zip?signature=temporary",
                }
            ],
        }

    def download_zip(self, full_zip_url, timeout_seconds):
        del full_zip_url, timeout_seconds
        self.calls.append(("download_zip", "NO_AUTH"))
        if self.failure == "download_crash":
            raise SystemExit("injected hard interruption")
        if self.failure == "download_timeout":
            raise TimeoutError("safe fake download timeout")
        return _result_zip(
            invalid=self.invalid_result,
            duplicate_content_list=self.duplicate_content_list,
        )


class _EmbeddingTransport:
    real_network = False

    def __init__(self, dimension: int = 3, *, invalid: str | None = None) -> None:
        self.dimension = dimension
        self.invalid = invalid
        self.calls: list[str] = []

    def embed(self, texts, idempotency_key, timeout_seconds):
        del timeout_seconds
        self.calls.append(idempotency_key)
        if self.invalid == "crash":
            raise SystemExit("injected embedding interruption")
        if self.invalid == "429":
            raise ProviderExecutionError(
                "EMBEDDING_RATE_LIMITED",
                status_code=429,
                provider_error_code="InvalidParameter",
                provider_error_type="invalid_request_error",
                outcome_unknown=False,
            )
        count = len(texts) - 1 if self.invalid == "count" else len(texts)
        dimension = self.dimension - 1 if self.invalid == "dimension" else self.dimension
        value = math.nan if self.invalid == "nan" else 0.5
        return [[value] * dimension for _ in range(count)]


class _StateSequenceTransport(_MinerUTransport):
    def __init__(self, states: list[str]) -> None:
        super().__init__()
        self.states = list(states)

    def batch_status(self, token, batch_id, timeout_seconds):
        del batch_id, timeout_seconds
        self.calls.append(("batch_status", token))
        state = self.states.pop(0)
        item = {"data_id": self.data_id, "state": state}
        if state in {"done", "completed", "success"}:
            item["full_zip_url"] = "https://download.example/result.zip?temporary=1"
        if state in {"failed", "error", "canceled", "cancelled"}:
            item["err_code"] = "-60001"
            item["err_msg"] = "sensitive provider failure detail"
        return {"extract_result": [item]}


class _OfficeTransport(_MinerUTransport):
    def download_zip(self, full_zip_url, timeout_seconds):
        del full_zip_url, timeout_seconds
        self.calls.append(("download_zip", "NO_AUTH"))
        return _office_result_zip()


def _pool():
    return MinerUTokenPool(
        ["token-secret-a", "token-secret-b"],  # noqa: S106
        max_concurrency_per_token=1,
        max_failures=1,
        cooldown_seconds=30,
    )


def _source(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "private-real-name.png"
    source.write_bytes(b"synthetic authorized bytes")
    return source, hashlib.sha256(source.read_bytes()).hexdigest()


def _result_store(tmp_path: Path) -> LocalProviderResultStore:
    return LocalProviderResultStore(tmp_path / "local-artifacts")


class _FailingResultStore:
    def __init__(self, *, hard: bool = False) -> None:
        self.hard = hard
        self.calls = 0

    def persist_mineru_result(self, anonymous_id, result_hash, zip_payload, nodes):
        del anonymous_id, result_hash, zip_payload, nodes
        self.calls += 1
        if self.hard:
            raise SystemExit("injected result-store interruption")
        raise OSError("injected atomic persistence failure")

    def read_mineru_nodes(self, artifact_id):
        del artifact_id
        raise AssertionError("unreachable")


class _AtomicWriteFailingStore(LocalProviderResultStore):
    def __init__(self, artifacts_root: Path) -> None:
        super().__init__(artifacts_root)
        self.write_count = 0

    def _write_durable(self, path: Path, payload: bytes) -> None:
        self.write_count += 1
        if self.write_count == 2:
            raise OSError("injected second-file failure")
        super()._write_durable(path, payload)


def test_mineru_official_batch_flow_round_robin_and_no_bearer_on_signed_put(
    tmp_path: Path,
) -> None:
    source, digest = _source(tmp_path)
    checkpoint = JsonCheckpointStore(tmp_path / "mineru.json")
    transport = _MinerUTransport()
    result_store = _result_store(tmp_path)
    runner = MinerUExecutionRunner(
        _pool(),
        transport,
        checkpoint,
        result_store,
        external_call_approved=False,
    )

    first = runner.run_file(source, "anonymous-1", digest)
    second = runner.run_file(source, "anonymous-2", digest)

    assert first["token_slot"] == 0 and second["token_slot"] == 1
    assert [operation for operation, _ in transport.calls].count("create_batch") == 2
    assert all(
        auth_marker == "NO_AUTH"
        for operation, auth_marker in transport.calls
        if operation == "put_signed"
    )
    assert "private-real-name" not in str(first) + checkpoint.path.read_text()
    assert "token-secret" not in str(first) + checkpoint.path.read_text()
    assert "http" not in checkpoint.path.read_text(encoding="utf-8")
    assert "signature" not in checkpoint.path.read_text(encoding="utf-8")
    assert "fake provider text" not in checkpoint.path.read_text(encoding="utf-8")
    assert "fake cell" not in checkpoint.path.read_text(encoding="utf-8")
    assert "fake item" not in checkpoint.path.read_text(encoding="utf-8")
    nodes = result_store.read_mineru_nodes(str(first["artifact_id"]))
    assert len(nodes) == 5
    assert nodes[0]["display_text"] == "fake provider text"
    assert nodes[0]["locator"] == {"page": 1, "bbox": [0.0, 0.0, 10.0, 10.0]}
    assert nodes[1]["content"]["table_body"].startswith("<table>")
    assert nodes[2]["content"]["list_items"] == ["fake item one", "fake item two"]
    assert "private-real-name" not in str(result_store.root)
    artifact_dir = result_store.root / str(first["artifact_id"])
    assert {path.name for path in artifact_dir.iterdir()} == {
        "manifest.json",
        "normalized-nodes.json",
        "provider-result.zip",
    }
    assert (
        hashlib.sha256((artifact_dir / "provider-result.zip").read_bytes()).hexdigest()
        == first["result_hash"]
    )
    assert (artifact_dir / "provider-result.zip").stat().st_size == first["artifact_bytes"]
    with zipfile.ZipFile(artifact_dir / "provider-result.zip") as archive:
        assert "sample-anonymous_content_list.json" in archive.namelist()
    aggregate = aggregate_persisted_mineru_evidence(
        [{"expected_locators": [{"page": 1}]}], [first], result_store
    )
    assert aggregate == {
        "completed_files": 1,
        "expected_locator_count": 1,
        "matched_locator_count": 1,
        "new_chunk_count": 5,
        "artifact_hash_count": 1,
        "embedding_scope_unchanged_chunks": 669,
        "content_in_output": False,
        "source_names_in_output": False,
    }


@pytest.mark.parametrize("failure", ["429", "timeout", "500"])
def test_mineru_retry_zero_failure_never_switches_token(tmp_path: Path, failure: str) -> None:
    source, digest = _source(tmp_path)
    transport = _MinerUTransport(failure=failure)
    runner = MinerUExecutionRunner(
        _pool(),
        transport,
        JsonCheckpointStore(tmp_path / "cp.json"),
        _result_store(tmp_path),
        external_call_approved=False,
    )
    with pytest.raises(ProviderExecutionError) as raised:
        runner.run_file(source, "anonymous", digest)
    assert len(transport.calls) == 1
    assert len({token for _, token in transport.calls}) == 1
    expected_code = {
        "429": "RATE_LIMIT",
        "timeout": "MINERU_TIMEOUT",
        "500": "MINERU_CREATE_BATCH_SERVER_ERROR",
    }[failure]
    assert raised.value.code == expected_code
    checkpoint_data = json.loads((tmp_path / "cp.json").read_text(encoding="utf-8"))
    persisted = json.dumps(checkpoint_data)
    assert expected_code in persisted
    expected_status = {"429": 429, "timeout": None, "500": 500}[failure]
    record = checkpoint_data["mineru"]["anonymous"]
    assert record.get("http_status") == expected_status
    expected_state = "FAILED" if failure == "429" else "UNKNOWN_OUTCOME"
    assert record["state"] == expected_state
    expected_second_error = "RATE_LIMIT" if failure == "429" else "UNKNOWN_OUTCOME"
    with pytest.raises(ProviderExecutionError, match=expected_second_error):
        runner.run_file(source, "anonymous", digest)
    assert len(transport.calls) == 1
    if failure == "429":
        assert record["provider_error_code"] == "A0202"
        assert record["provider_error_category"] == "AUTHENTICATION_OR_TOKEN"
        assert record["trace_id_hash"] == "a" * 64


def test_mineru_budget_zip_schema_unknown_outcome_and_documented_docx(tmp_path: Path) -> None:
    source, digest = _source(tmp_path)
    checkpoints = JsonCheckpointStore(tmp_path / "cp.json")
    invalid = _MinerUTransport(invalid_result=True)
    runner = MinerUExecutionRunner(
        _pool(),
        invalid,
        checkpoints,
        _result_store(tmp_path),
        external_call_approved=False,
        max_files=1,
    )
    with pytest.raises(ProviderExecutionError, match="ZIP"):
        runner.run_file(source, "anonymous-1", digest)
    calls = len(invalid.calls)
    with pytest.raises(ProviderExecutionError, match="FILE_BUDGET"):
        runner.run_file(source, "anonymous-2", digest)
    assert len(invalid.calls) == calls

    unknown = JsonCheckpointStore(tmp_path / "unknown.json")
    unknown.save(
        "mineru",
        "anonymous",
        {"state": "SIGNED_PUT_IN_FLIGHT", "token_slot": 0, "snapshot_hash": digest},
    )
    transport = _MinerUTransport()
    with pytest.raises(ProviderExecutionError, match="UNKNOWN_OUTCOME"):
        MinerUExecutionRunner(
            _pool(),
            transport,
            unknown,
            _result_store(tmp_path),
            external_call_approved=False,
        ).run_file(source, "anonymous", digest)
    assert transport.calls == []

    documented = MinerUCapabilityProbe("https://mineru.net/api/v4").probe()
    custom = MinerUCapabilityProbe("https://private.example/mineru", custom_deployment=True).probe()
    assert documented["status"] == "DOCX_DOCUMENTED_SUPPORTED"
    assert documented["docx_supported"] is True
    assert documented["request_count"] == 0
    assert documented["real_docx_execution_approved"] is False
    assert "xlsx" in documented["supported_extensions"]
    assert custom["status"] == "CAPABILITY_UNCONFIRMED"
    assert custom["request_count"] == 0


@pytest.mark.parametrize("failure", ["put_timeout", "download_timeout"])
def test_mineru_uncertain_windows_persist_no_signed_url(tmp_path: Path, failure: str) -> None:
    source, digest = _source(tmp_path)
    checkpoints = JsonCheckpointStore(tmp_path / f"{failure}.json")
    runner = MinerUExecutionRunner(
        _pool(),
        _MinerUTransport(failure=failure),
        checkpoints,
        _result_store(tmp_path),
        external_call_approved=False,
    )
    with pytest.raises(ProviderExecutionError):
        runner.run_file(source, "anonymous", digest)
    persisted = checkpoints.path.read_text(encoding="utf-8")
    assert "UNKNOWN_OUTCOME" in persisted
    assert "http" not in persisted
    assert "signature" not in persisted


@pytest.mark.parametrize("failure", ["create_crash", "put_crash", "download_crash"])
def test_mineru_hard_interruption_leaves_manual_reconciliation_checkpoint(
    tmp_path: Path, failure: str
) -> None:
    source, digest = _source(tmp_path)
    checkpoints = JsonCheckpointStore(tmp_path / f"{failure}.json")
    runner = MinerUExecutionRunner(
        _pool(),
        _MinerUTransport(failure=failure),
        checkpoints,
        _result_store(tmp_path),
        external_call_approved=False,
    )
    with pytest.raises(SystemExit, match="hard interruption"):
        runner.run_file(source, "anonymous", digest)
    persisted = checkpoints.path.read_text(encoding="utf-8")
    assert "UNKNOWN_OUTCOME" in persisted
    assert "http" not in persisted
    assert "signature" not in persisted


@pytest.mark.parametrize("hard", [False, True])
def test_mineru_result_persistence_failure_never_resubmits(tmp_path: Path, hard: bool) -> None:
    source, digest = _source(tmp_path)
    checkpoints = JsonCheckpointStore(tmp_path / f"persist-{hard}.json")
    transport = _MinerUTransport()
    result_store = _FailingResultStore(hard=hard)
    runner = MinerUExecutionRunner(
        _pool(),
        transport,
        checkpoints,
        result_store,
        external_call_approved=False,
    )
    if hard:
        with pytest.raises(SystemExit, match="result-store interruption"):
            runner.run_file(source, "anonymous", digest)
    else:
        with pytest.raises(ProviderExecutionError) as raised:
            runner.run_file(source, "anonymous", digest)
        assert raised.value.code == "MINERU_RESULT_PERSIST_FAILURE"
    call_count = len(transport.calls)
    persisted = checkpoints.path.read_text(encoding="utf-8")
    assert "UNKNOWN_OUTCOME" in persisted
    assert "PERSIST_RESULT" in persisted
    assert "http" not in persisted
    assert "fake provider text" not in persisted
    with pytest.raises(ProviderExecutionError, match="UNKNOWN_OUTCOME"):
        runner.run_file(source, "anonymous", digest)
    assert len(transport.calls) == call_count
    assert result_store.calls == 1


def test_local_result_store_failure_leaves_no_partial_artifact(tmp_path: Path) -> None:
    store = _AtomicWriteFailingStore(tmp_path / "atomic-artifacts")
    payload = _result_zip()
    result_hash = hashlib.sha256(payload).hexdigest()
    nodes = [
        {
            "anonymous_sample_id": "anonymous",
            "type": "text",
            "display_text": "synthetic local content",
            "locator": {"page": 1, "bbox": [0.0, 0.0, 1.0, 1.0]},
        }
    ]
    with pytest.raises(OSError, match="second-file failure"):
        store.persist_mineru_result("anonymous", result_hash, payload, nodes)
    assert store.root.is_dir()
    assert list(store.root.iterdir()) == []


def test_mineru_crash_after_atomic_persist_is_manually_recoverable(
    tmp_path: Path,
) -> None:
    source, digest = _source(tmp_path)
    checkpoints = JsonCheckpointStore(tmp_path / "post-persist-crash.json")
    transport = _MinerUTransport()
    result_store = _result_store(tmp_path)

    def crash_after_persist(artifact: object) -> None:
        del artifact
        raise SystemExit("injected post-persist interruption")

    runner = MinerUExecutionRunner(
        _pool(),
        transport,
        checkpoints,
        result_store,
        external_call_approved=False,
        after_result_persist=crash_after_persist,
    )
    with pytest.raises(SystemExit, match="post-persist interruption"):
        runner.run_file(source, "anonymous", digest)
    persisted = checkpoints.path.read_text(encoding="utf-8")
    assert "UNKNOWN_OUTCOME" in persisted
    assert "PERSIST_RESULT" in persisted
    artifact_directories = [path for path in result_store.root.iterdir() if path.is_dir()]
    assert len(artifact_directories) == 1
    nodes = result_store.read_mineru_nodes(artifact_directories[0].name)
    assert nodes[0]["locator"] == {"page": 1, "bbox": [0.0, 0.0, 10.0, 10.0]}
    call_count = len(transport.calls)
    with pytest.raises(ProviderExecutionError, match="UNKNOWN_OUTCOME"):
        runner.run_file(source, "anonymous", digest)
    assert len(transport.calls) == call_count


def test_mineru_official_content_list_is_unique_and_polling_is_throttled(
    tmp_path: Path,
) -> None:
    source, digest = _source(tmp_path)
    current = [0.0]
    sleeps: list[float] = []

    def sleep_and_advance(seconds: float) -> None:
        sleeps.append(seconds)
        current[0] += seconds

    transport = _MinerUTransport(pending_polls=2)
    result = MinerUExecutionRunner(
        _pool(),
        transport,
        JsonCheckpointStore(tmp_path / "poll.json"),
        _result_store(tmp_path),
        external_call_approved=False,
        clock=lambda: current[0],
        sleeper=sleep_and_advance,
    ).run_file(source, "anonymous", digest)
    assert result["node_count"] == 5
    assert sleeps == [10, 10]

    ambiguous = MinerUExecutionRunner(
        _pool(),
        _MinerUTransport(duplicate_content_list=True),
        JsonCheckpointStore(tmp_path / "ambiguous.json"),
        _result_store(tmp_path),
        external_call_approved=False,
    )
    with pytest.raises(ProviderExecutionError, match="AMBIGUOUS"):
        ambiguous.run_file(source, "anonymous", digest)


def test_mineru_documented_intermediate_states_poll_until_done(tmp_path: Path) -> None:
    source, digest = _source(tmp_path)
    current = [0.0]
    sleeps: list[float] = []

    def sleep_and_advance(seconds: float) -> None:
        sleeps.append(seconds)
        current[0] += seconds

    transport = _StateSequenceTransport(["waiting-file", "converting", "running", "done"])
    result = MinerUExecutionRunner(
        _pool(),
        transport,
        JsonCheckpointStore(tmp_path / "intermediate.json"),
        _result_store(tmp_path),
        external_call_approved=False,
        clock=lambda: current[0],
        sleeper=sleep_and_advance,
    ).run_file(source, "anonymous", digest)
    assert result["state"] == "COMPLETED"
    assert sleeps == [10, 10, 10]
    assert [operation for operation, _ in transport.calls].count("batch_status") == 4


def test_mineru_explicit_failed_state_is_deterministic_and_drops_err_msg(
    tmp_path: Path,
) -> None:
    source, digest = _source(tmp_path)
    checkpoints = JsonCheckpointStore(tmp_path / "explicit-failed.json")
    transport = _StateSequenceTransport(["failed"])
    runner = MinerUExecutionRunner(
        _pool(),
        transport,
        checkpoints,
        _result_store(tmp_path),
        external_call_approved=False,
    )
    with pytest.raises(ProviderExecutionError, match="EXPLICIT_FAILED"):
        runner.run_file(source, "anonymous", digest)
    persisted = checkpoints.path.read_text(encoding="utf-8")
    assert '"state": "FAILED"' in persisted
    assert '"provider_state": "failed"' in persisted
    assert '"provider_error_code": "-60001"' in persisted
    assert "sensitive provider failure detail" not in persisted


def test_mineru_unknown_safe_state_is_unsupported_not_provider_failed(
    tmp_path: Path,
) -> None:
    source, digest = _source(tmp_path)
    checkpoints = JsonCheckpointStore(tmp_path / "unsupported.json")
    transport = _StateSequenceTransport(["queued-new"])
    runner = MinerUExecutionRunner(
        _pool(),
        transport,
        checkpoints,
        _result_store(tmp_path),
        external_call_approved=False,
    )
    with pytest.raises(ProviderExecutionError, match="STATE_UNSUPPORTED"):
        runner.run_file(source, "anonymous", digest)
    persisted = checkpoints.path.read_text(encoding="utf-8")
    assert '"state": "UNSUPPORTED_PROVIDER_STATE"' in persisted
    assert '"provider_state": "queued-new"' in persisted
    assert "EXPLICIT_FAILED" not in persisted
    call_count = len(transport.calls)
    with pytest.raises(ProviderExecutionError, match="STATE_UNSUPPORTED"):
        runner.run_file(source, "anonymous", digest)
    assert len(transport.calls) == call_count


def test_mineru_office_allows_page_without_bbox_but_scan_scope_rejects_it(
    tmp_path: Path,
) -> None:
    source, digest = _source(tmp_path)
    docx_store = _result_store(tmp_path / "docx")
    docx = MinerUExecutionRunner(
        _pool(),
        _OfficeTransport(),
        JsonCheckpointStore(tmp_path / "docx-office.json"),
        docx_store,
        external_call_approved=False,
        attempt_revision="docx-office-test",
        scope="docx",
        locator_policy="office_page_bbox_optional",
    ).run_file(source, "docx-anonymous", digest, is_ocr=False)
    nodes = docx_store.read_mineru_nodes(str(docx["artifact_id"]))
    assert nodes[0]["locator"] == {"page": 1}
    assert nodes[1]["type"] == "index"
    assert nodes[1]["locator"] == {"page": 2}

    with pytest.raises(ProviderExecutionError, match="CONTENT_LOCATOR_INVALID"):
        MinerUExecutionRunner(
            _pool(),
            _OfficeTransport(),
            JsonCheckpointStore(tmp_path / "scan-strict.json"),
            _result_store(tmp_path / "scan"),
            external_call_approved=False,
            attempt_revision="scan-strict-test",
            scope="pdf_scanned_or_image",
        ).run_file(source, "scan-anonymous", digest)


def test_docx_recovery_uses_only_status_and_download_and_preserves_original(
    tmp_path: Path,
) -> None:
    original_path = tmp_path / "original-docx-v1.json"
    original = {
        "state": "FAILED",
        "error_code": "MINERU_CONTENT_LOCATOR_INVALID",
        "batch_id": "opaque-existing-batch",
        "token_slot": 0,
        "poll_count": 2,
    }
    original_path.write_text(json.dumps(original), encoding="utf-8")
    original_bytes = original_path.read_bytes()
    checkpoints = JsonCheckpointStore(tmp_path / "recovery.json")
    result_store = _result_store(tmp_path / "recovery-artifacts")
    transport = _OfficeTransport()
    transport.data_id = "docx-anonymous"
    validator = MinerUExecutionRunner(
        _pool(),
        transport,
        checkpoints,
        result_store,
        external_call_approved=False,
        attempt_revision="recovery-validator-test",
        scope="docx",
        locator_policy="office_page_bbox_optional",
    )
    recovery = MinerUDocxRecoveryRunner(
        _pool(),
        transport,
        checkpoints,
        result_store,
        validator.validate_result_zip,
        external_call_approved=False,
    ).run(original, "docx-anonymous")
    operations = [operation for operation, _ in transport.calls]
    assert operations.count("create_batch") == 0
    assert operations.count("put_signed") == 0
    assert operations.count("batch_status") == 1
    assert operations.count("download_zip") == 1
    assert recovery["create_count"] == recovery["upload_count"] == 0
    assert recovery["node_count"] == recovery["locator_count"] == 2
    assert original_path.read_bytes() == original_bytes
    persisted = checkpoints.path.read_text(encoding="utf-8")
    assert "office page one" not in persisted
    assert "https://" not in persisted


def test_real_network_mineru_guard_precedes_file_read() -> None:
    transport = _MinerUTransport()
    transport.real_network = True
    runner = MinerUExecutionRunner(
        _pool(),
        transport,
        JsonCheckpointStore(Path("unused.json")),
        LocalProviderResultStore(Path("unused-artifacts")),
        external_call_approved=False,
    )
    with pytest.raises(ExecutionApprovalRequired):
        runner.run_file(Path("never-read"), "anonymous", "0" * 64)
    assert transport.calls == []


def test_provider_egress_requires_configured_region_and_allowed_classification() -> None:
    with pytest.raises(ExecutionApprovalRequired, match="REGION_NOT_APPROVED"):
        require_configured_provider_egress(
            outbound_ai_allowed=True,
            allowed_classifications=["public", "confidential"],
            approved_processing_regions=[],
            classifications=["confidential"],
        )
    require_configured_provider_egress(
        outbound_ai_allowed=True,
        allowed_classifications=["public", "confidential"],
        approved_processing_regions=["approved-region"],
        classifications=["public", "confidential"],
    )
    with pytest.raises(ExecutionApprovalRequired, match="EGRESS_POLICY_DENIED"):
        require_configured_provider_egress(
            outbound_ai_allowed=True,
            allowed_classifications=["public", "confidential", "restricted"],
            approved_processing_regions=["approved-region"],
            classifications=["restricted"],
        )


@pytest.mark.parametrize(
    ("provider_code", "category"),
    [
        ("A0202", "AUTHENTICATION_OR_TOKEN"),
        ("A0211", "AUTHENTICATION_OR_TOKEN"),
        ("-500", "PROVIDER_INTERNAL"),
        ("-10002", "REQUEST_OR_QUOTA"),
        ("-60001", "FILE_OR_TASK"),
        ("unlisted", "PROVIDER_BUSINESS_ERROR_UNCLASSIFIED"),
    ],
)
def test_mineru_official_error_codes_map_to_safe_categories(
    provider_code: str, category: str
) -> None:
    assert mineru_provider_error_category(provider_code) == category


def _chunks(count: int) -> list[EmbeddingChunk]:
    return [
        EmbeddingChunk(f"chunk-{index:04d}", f"controlled text {index}") for index in range(count)
    ]


def test_embedding_669_chunks_21_batches_checkpoint_resume_without_duplicate(
    tmp_path: Path,
) -> None:
    transport = _EmbeddingTransport()
    checkpoints = JsonCheckpointStore(tmp_path / "embedding.json")

    def crash_after_first(batch_index: int) -> None:
        if batch_index == 0:
            raise RuntimeError("injected crash after durable checkpoint")

    with pytest.raises(RuntimeError, match="injected crash"):
        EmbeddingExecutionRunner(
            transport,
            checkpoints,
            dimension=3,
            external_call_approved=False,
            max_batches=21,
            after_checkpoint=crash_after_first,
        ).run(_chunks(669))
    resumed = EmbeddingExecutionRunner(
        transport,
        checkpoints,
        dimension=3,
        external_call_approved=False,
        max_batches=21,
    ).run(_chunks(669))
    assert resumed["batch_count"] == resumed["completed_batches"] == 21
    assert len(transport.calls) == 21
    assert resumed["automatic_retries"] == 0
    assert resumed["zilliz_write_performed"] is False
    persisted = checkpoints.path.read_text(encoding="utf-8")
    assert "controlled text" not in persisted
    assert "chunk-0000" in persisted


@pytest.mark.parametrize("invalid", ["count", "dimension", "nan"])
def test_embedding_schema_failures_are_fail_closed(tmp_path: Path, invalid: str) -> None:
    transport = _EmbeddingTransport(invalid=invalid)
    runner = EmbeddingExecutionRunner(
        transport,
        JsonCheckpointStore(tmp_path / f"{invalid}.json"),
        dimension=3,
        external_call_approved=False,
        max_batches=21,
    )
    with pytest.raises(ProviderExecutionError):
        runner.run(_chunks(2))
    assert len(transport.calls) == 1
    with pytest.raises(ProviderExecutionError):
        runner.run(_chunks(2))
    assert len(transport.calls) == 1


def test_embedding_budget_snapshot_and_network_guards_precede_calls(tmp_path: Path) -> None:
    transport = _EmbeddingTransport()
    checkpoints = JsonCheckpointStore(tmp_path / "budget.json")
    runner = EmbeddingExecutionRunner(
        transport,
        checkpoints,
        dimension=3,
        external_call_approved=False,
        max_batches=21,
    )
    with pytest.raises(ProviderExecutionError, match="CHUNK_BUDGET"):
        runner.run(_chunks(670))
    assert transport.calls == []
    runner.run(_chunks(2))
    with pytest.raises(ProviderExecutionError, match="SNAPSHOT"):
        runner.run([EmbeddingChunk("chunk-0000", "changed"), _chunks(2)[1]])

    network = _EmbeddingTransport()
    network.real_network = True
    with pytest.raises(ExecutionApprovalRequired):
        EmbeddingExecutionRunner(
            network,
            JsonCheckpointStore(tmp_path / "network.json"),
            dimension=3,
            external_call_approved=False,
            max_batches=21,
        ).run(_chunks(1))
    assert network.calls == []


def test_dashscope_v4_batch_11_fails_before_network_or_checkpoint(tmp_path: Path) -> None:
    transport = _EmbeddingTransport(dimension=3)
    checkpoint_path = tmp_path / "must-not-exist.json"
    contract = embedding_provider_contract(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="text-embedding-v4",
        dimension=1024,
        configured_batch_size=11,
        chunk_count=669,
        approved_max_batches=67,
    )
    assert contract["required_new_batches"] == 67
    assert contract["configuration_valid"] is False
    with pytest.raises(ProviderExecutionError, match="BATCH_SIZE_EXCEEDS"):
        require_embedding_provider_contract(contract)
    assert transport.calls == []
    assert not checkpoint_path.exists()


def test_dashscope_v4_batch_10_uses_67_batches_and_new_attempt_checkpoint(
    tmp_path: Path,
) -> None:
    contract = embedding_provider_contract(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="text-embedding-v4",
        dimension=1024,
        configured_batch_size=10,
        chunk_count=669,
        approved_max_batches=67,
    )
    require_embedding_provider_contract(contract)
    assert contract["configuration_valid"] is True
    assert contract["required_new_batches"] == 67

    old_checkpoint = tmp_path / "embedding.json"
    old_checkpoint.write_bytes(b'{"old_failed_attempt":"preserve-byte-for-byte"}\n')
    old_bytes = old_checkpoint.read_bytes()
    new_checkpoint = tmp_path / "embedding-attempt-v2.json"
    transport = _EmbeddingTransport(dimension=3)
    result = EmbeddingExecutionRunner(
        transport,
        JsonCheckpointStore(new_checkpoint),
        dimension=3,
        external_call_approved=False,
        batch_size=10,
        max_chunks=669,
        max_batches=67,
    ).run(_chunks(669))

    assert result["batch_count"] == result["completed_batches"] == 67
    assert len(transport.calls) == 67
    assert result["automatic_retries"] == 0
    assert result["zilliz_write_performed"] is False
    assert old_checkpoint.read_bytes() == old_bytes
    assert new_checkpoint.is_file()
    persisted = new_checkpoint.read_text(encoding="utf-8")
    assert "controlled text" not in persisted
    assert "chunk-0000" in persisted


def test_embedding_transport_error_is_preserved_and_never_retried(tmp_path: Path) -> None:
    transport = _EmbeddingTransport(invalid="429")
    checkpoints = JsonCheckpointStore(tmp_path / "rate-limit.json")
    runner = EmbeddingExecutionRunner(
        transport,
        checkpoints,
        dimension=3,
        external_call_approved=False,
        max_batches=21,
    )
    with pytest.raises(ProviderExecutionError) as raised:
        runner.run(_chunks(2))
    assert raised.value.code == "EMBEDDING_RATE_LIMITED"
    assert len(transport.calls) == 1
    persisted = checkpoints.path.read_text(encoding="utf-8")
    assert '"state": "FAILED"' in persisted
    assert "EMBEDDING_RATE_LIMITED" in persisted
    assert '"http_status": 429' in persisted
    assert '"provider_error_code": "InvalidParameter"' in persisted
    assert '"provider_error_type": "invalid_request_error"' in persisted
    assert "controlled text" not in persisted
    with pytest.raises(ProviderExecutionError, match="EMBEDDING_RATE_LIMITED"):
        runner.run(_chunks(2))
    assert len(transport.calls) == 1


def test_embedding_hard_interruption_is_not_retried(tmp_path: Path) -> None:
    transport = _EmbeddingTransport(invalid="crash")
    checkpoints = JsonCheckpointStore(tmp_path / "embedding-crash.json")
    runner = EmbeddingExecutionRunner(
        transport,
        checkpoints,
        dimension=3,
        external_call_approved=False,
        max_batches=21,
    )
    with pytest.raises(SystemExit, match="embedding interruption"):
        runner.run(_chunks(2))
    persisted = checkpoints.path.read_text(encoding="utf-8")
    assert "UNKNOWN_OUTCOME" in persisted
    assert "controlled text" not in persisted
    with pytest.raises(ProviderExecutionError, match="UNKNOWN_OUTCOME"):
        runner.run(_chunks(2))
    assert len(transport.calls) == 1
