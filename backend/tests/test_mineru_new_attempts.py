from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
import yaml
from ragkb.adapters.mineru_pool import MinerUTokenPool
from ragkb.application.provider_runners import MinerUExecutionRunner
from ragkb.contracts.provider_execution import ProviderExecutionError
from ragkb.evaluation.format_samples import _resolve
from ragkb.evaluation.local_sample_validation import aggregate_persisted_mineru_evidence
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore
from ragkb.infrastructure.provider_results import LocalProviderResultStore

from scripts.run_mineru_provider import (
    ATTEMPTS,
    DOCX_RECOVERY_CHECKPOINT_SHA256,
    DOCX_V1_CHECKPOINT_SHA256,
    LEGACY_MINERU_CHECKPOINT_SHA256,
    ROOT,
    SCAN_V2_CHECKPOINT_SHA256,
    SCAN_V3_CHECKPOINT_SHA256,
    SCAN_V4_CHECKPOINT_SHA256,
    SCAN_V5_CHECKPOINT_SHA256,
    _preflight_attempt,
    build_attempt_plan,
)


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "anonymous_content_list.json",
            json.dumps(
                [
                    {
                        "type": "text",
                        "text": "synthetic page one",
                        "page_idx": 0,
                        "bbox": [0, 0, 10, 10],
                    },
                    {
                        "type": "text",
                        "text": "synthetic page two",
                        "page_idx": 1,
                        "bbox": [0, 0, 10, 10],
                    },
                ]
            ),
        )
    return buffer.getvalue()


class _Transport:
    real_network = False

    def __init__(self) -> None:
        self.names: list[tuple[str, bool]] = []
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
        del token, page_ranges, model_version, enable_table, enable_formula, timeout_seconds
        self.names.append((anonymous_name, is_ocr))
        self.data_id = data_id
        return {
            "batch_id": "opaque-batch",
            "file_url": "https://upload.example/signed?temporary=1",
        }

    def put_signed(self, file_url, source, timeout_seconds):
        del file_url, source, timeout_seconds

    def batch_status(self, token, batch_id, timeout_seconds):
        del token, batch_id, timeout_seconds
        return {
            "extract_result": [
                {
                    "data_id": self.data_id,
                    "state": "done",
                    "full_zip_url": "https://download.example/result.zip?temporary=1",
                }
            ]
        }

    def download_zip(self, full_zip_url, timeout_seconds):
        del full_zip_url, timeout_seconds
        return _zip()


def _pool() -> MinerUTokenPool[object]:
    return MinerUTokenPool(
        ["synthetic-token"],  # noqa: S106
        max_concurrency_per_token=1,
        max_failures=2,
        cooldown_seconds=30,
        failover_enabled=False,
    )


def _run_scope(
    tmp_path: Path,
    *,
    attempt_revision: str,
    scope: str,
    suffix: str,
    is_ocr: bool,
    count: int = 10,
    max_files: int = 10,
    max_requests: int = 330,
) -> tuple[list[dict[str, object]], JsonCheckpointStore, LocalProviderResultStore, _Transport]:
    transport = _Transport()
    checkpoints = JsonCheckpointStore(tmp_path / f"{scope}.json")
    result_store = LocalProviderResultStore(tmp_path / f"{scope}-artifacts")
    runner = MinerUExecutionRunner(
        _pool(),
        transport,
        checkpoints,
        result_store,
        external_call_approved=False,
        attempt_revision=attempt_revision,
        scope=scope,
        max_files=max_files,
        max_requests=max_requests,
        max_polls_per_file=30,
        poll_interval_seconds=10,
    )
    source = tmp_path / f"anonymous{suffix}"
    source.write_bytes(b"synthetic authorized bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    results = [
        runner.run_file(
            source,
            f"{scope}-{index:02d}",
            digest,
            is_ocr=is_ocr,
        )
        for index in range(count)
    ]
    with pytest.raises(ProviderExecutionError, match="FILE_BUDGET"):
        runner.run_file(source, f"{scope}-overflow", digest, is_ocr=is_ocr)
    return results, checkpoints, result_store, transport


def test_fixed_attempt_plan_and_real_source_preflights_are_network_free(
    tmp_path: Path,
) -> None:
    plan_root = tmp_path / "empty-plan-checkpoints"
    plan = build_attempt_plan(plan_root)
    assert plan["new_real_call_count"] == 0
    assert plan["legacy_checkpoint_mutation_allowed"] is False
    attempts = {item["checkpoint_name"]: item for item in plan["attempts"]}
    assert set(attempts) == {
        "mineru-scan-attempt-v2.json",
        "mineru-scan-attempt-v3.json",
        "mineru-scan-attempt-v4.json",
        "mineru-scan-attempt-v5.json",
        "mineru-docx-attempt-v1.json",
        "mineru-docx-attempt-v2.json",
        "mineru-docx-pdf-attempt-v1.json",
    }
    assert attempts["mineru-scan-attempt-v5.json"]["expected_locator_count"] == 6
    assert attempts["mineru-scan-attempt-v5.json"]["max_files"] == 6
    assert attempts["mineru-scan-attempt-v5.json"]["approved_by_user"] is True
    assert attempts["mineru-scan-attempt-v5.json"]["derived_input_prepared"] is True
    assert attempts["mineru-docx-attempt-v1.json"]["expected_locator_count"] == 20
    assert attempts["mineru-docx-attempt-v1.json"]["approved_by_user"] is True
    assert attempts["mineru-docx-attempt-v2.json"]["expected_locator_count"] == 18
    assert attempts["mineru-docx-attempt-v2.json"]["max_files"] == 9
    assert attempts["mineru-docx-attempt-v2.json"]["approved_by_user"] is False
    assert plan["docx_recovery"]["approved_by_user"] is False
    assert attempts["mineru-docx-pdf-attempt-v1.json"]["expected_locator_count"] == 20
    assert attempts["mineru-docx-pdf-attempt-v1.json"]["approved_by_user"] is True
    assert attempts["mineru-docx-attempt-v1.json"]["execution_status"] == (
        "BLOCKED_BY_COMBINED_SCAN_PREREQUISITE"
    )
    assert all(item["executed"] is False for item in attempts.values())

    checkpoint_root = tmp_path / "provider-checkpoints"
    checkpoint_root.mkdir()
    checkpoint_root.joinpath("mineru.json").write_bytes(
        (ROOT / "artifacts/final-validation/provider-checkpoints/mineru.json").read_bytes()
    )
    checkpoint_root.joinpath("mineru-scan-attempt-v2.json").write_bytes(
        (
            ROOT / "artifacts/final-validation/provider-checkpoints/mineru-scan-attempt-v2.json"
        ).read_bytes()
    )
    checkpoint_root.joinpath("mineru-scan-attempt-v3.json").write_bytes(
        (
            ROOT / "artifacts/final-validation/provider-checkpoints/mineru-scan-attempt-v3.json"
        ).read_bytes()
    )
    checkpoint_root.joinpath("mineru-scan-attempt-v4.json").write_bytes(
        (
            ROOT / "artifacts/final-validation/provider-checkpoints/mineru-scan-attempt-v4.json"
        ).read_bytes()
    )

    manifest = yaml.safe_load(
        (ROOT / "backend/tests/fixtures/manifests/format-samples.yaml").read_text(encoding="utf-8")
    )
    scan_v5 = ATTEMPTS["execute-scan-v5"]
    scan_item = next(
        value for value in manifest["collection_plan"] if value["format"] == "pdf_scanned_or_image"
    )
    scan_metadata = yaml.safe_load(
        _resolve(ROOT, str(scan_item["metadata_path"])).read_text(encoding="utf-8")
    )
    _, samples = _preflight_attempt(scan_v5, scan_item, scan_metadata, checkpoint_root)
    assert len(samples) == 6

    checkpoint_root.joinpath("mineru-scan-attempt-v5.json").write_bytes(
        (
            ROOT / "artifacts/final-validation/provider-checkpoints/mineru-scan-attempt-v5.json"
        ).read_bytes()
    )
    checkpoint_root.joinpath("mineru-docx-attempt-v1.json").write_bytes(
        (
            ROOT / "artifacts/final-validation/provider-checkpoints/mineru-docx-attempt-v1.json"
        ).read_bytes()
    )
    checkpoint_root.joinpath("mineru-docx-recovery-v1.json").write_bytes(
        (
            ROOT / "artifacts/final-validation/provider-checkpoints/mineru-docx-recovery-v1.json"
        ).read_bytes()
    )
    docx = ATTEMPTS["execute-docx-v2"]
    docx_item = next(value for value in manifest["collection_plan"] if value["format"] == "docx")
    docx_metadata = yaml.safe_load(
        _resolve(ROOT, str(docx_item["metadata_path"])).read_text(encoding="utf-8")
    )
    with pytest.raises(RuntimeError, match="RECOVERY_LOCATOR_GATE"):
        _preflight_attempt(docx, docx_item, docx_metadata, checkpoint_root)
    docx_pdf = ATTEMPTS["execute-docx-pdf-v1"]
    _, docx_pdf_samples = _preflight_attempt(docx_pdf, docx_item, docx_metadata, checkpoint_root)
    assert len(docx_pdf_samples) == 10


def test_scan_and_docx_attempts_have_isolated_budgets_locators_and_names(
    tmp_path: Path,
) -> None:
    legacy = ROOT / "artifacts/final-validation/provider-checkpoints/mineru.json"
    before = hashlib.sha256(legacy.read_bytes()).hexdigest()
    assert before == LEGACY_MINERU_CHECKPOINT_SHA256
    scan_v2 = ROOT / "artifacts/final-validation/provider-checkpoints/mineru-scan-attempt-v2.json"
    scan_v2_before = hashlib.sha256(scan_v2.read_bytes()).hexdigest()
    assert scan_v2_before == SCAN_V2_CHECKPOINT_SHA256
    scan_v3 = ROOT / "artifacts/final-validation/provider-checkpoints/mineru-scan-attempt-v3.json"
    scan_v3_before = hashlib.sha256(scan_v3.read_bytes()).hexdigest()
    assert scan_v3_before == SCAN_V3_CHECKPOINT_SHA256
    scan_v4 = ROOT / "artifacts/final-validation/provider-checkpoints/mineru-scan-attempt-v4.json"
    scan_v4_before = hashlib.sha256(scan_v4.read_bytes()).hexdigest()
    assert scan_v4_before == SCAN_V4_CHECKPOINT_SHA256
    scan_v5 = ROOT / "artifacts/final-validation/provider-checkpoints/mineru-scan-attempt-v5.json"
    scan_v5_before = hashlib.sha256(scan_v5.read_bytes()).hexdigest()
    assert scan_v5_before == SCAN_V5_CHECKPOINT_SHA256
    docx_v1 = ROOT / "artifacts/final-validation/provider-checkpoints/mineru-docx-attempt-v1.json"
    docx_v1_before = hashlib.sha256(docx_v1.read_bytes()).hexdigest()
    assert docx_v1_before == DOCX_V1_CHECKPOINT_SHA256
    recovery = ROOT / "artifacts/final-validation/provider-checkpoints/mineru-docx-recovery-v1.json"
    recovery_before = hashlib.sha256(recovery.read_bytes()).hexdigest()
    assert recovery_before == DOCX_RECOVERY_CHECKPOINT_SHA256

    scan_results, scan_cp, scan_store, scan_transport = _run_scope(
        tmp_path,
        attempt_revision="mineru-scan-attempt:v4",
        scope="pdf_scanned_or_image",
        suffix=".png",
        is_ocr=True,
    )
    docx_results, docx_cp, docx_store, docx_transport = _run_scope(
        tmp_path,
        attempt_revision="mineru-docx-attempt:v1",
        scope="docx",
        suffix=".docx",
        is_ocr=False,
    )
    scan_samples = [{"expected_locators": [{"page": 1}]} for _ in range(10)]
    docx_samples = [{"expected_locators": [{"page": 1}, {"page": 2}]} for _ in range(10)]
    scan_evidence = aggregate_persisted_mineru_evidence(scan_samples, scan_results, scan_store)
    docx_evidence = aggregate_persisted_mineru_evidence(docx_samples, docx_results, docx_store)
    assert scan_evidence["matched_locator_count"] == 10
    assert scan_evidence["expected_locator_count"] == 10
    assert docx_evidence["matched_locator_count"] == 20
    assert docx_evidence["expected_locator_count"] == 20
    assert all(name.endswith(".png") and is_ocr for name, is_ocr in scan_transport.names)
    assert all(name.endswith(".docx") and not is_ocr for name, is_ocr in docx_transport.names)

    scan_data = json.loads(scan_cp.path.read_text(encoding="utf-8"))["mineru"]
    docx_data = json.loads(docx_cp.path.read_text(encoding="utf-8"))["mineru"]
    assert scan_data["_manifest"]["scope"] == "pdf_scanned_or_image"
    assert docx_data["_manifest"]["scope"] == "docx"
    assert scan_data["_manifest"]["max_files"] == 10
    assert docx_data["_manifest"]["max_files"] == 10
    assert scan_data["_manifest"]["max_requests"] == 330
    assert docx_data["_manifest"]["max_requests"] == 330
    serialized = json.dumps({"scan": scan_data, "docx": docx_data})
    assert "synthetic authorized bytes" not in serialized
    assert "https://" not in serialized
    assert "synthetic-token" not in serialized
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == before
    assert hashlib.sha256(scan_v2.read_bytes()).hexdigest() == scan_v2_before
    assert hashlib.sha256(scan_v3.read_bytes()).hexdigest() == scan_v3_before
    assert hashlib.sha256(scan_v4.read_bytes()).hexdigest() == scan_v4_before
    assert hashlib.sha256(scan_v5.read_bytes()).hexdigest() == scan_v5_before
    assert hashlib.sha256(docx_v1.read_bytes()).hexdigest() == docx_v1_before
    assert hashlib.sha256(recovery.read_bytes()).hexdigest() == recovery_before


def test_scan_v5_sends_only_six_remaining_and_combines_to_ten_locators(
    tmp_path: Path,
) -> None:
    v4_results, _, v4_store, v4_transport = _run_scope(
        tmp_path,
        attempt_revision="mineru-scan-attempt:v4-test",
        scope="scan-v4-test",
        suffix=".png",
        is_ocr=True,
        count=4,
        max_files=4,
        max_requests=132,
    )
    v5_results, _, v5_store, v5_transport = _run_scope(
        tmp_path,
        attempt_revision="mineru-scan-attempt:v5-test",
        scope="scan-v5-test",
        suffix=".png",
        is_ocr=True,
        count=6,
        max_files=6,
        max_requests=198,
    )
    v4_evidence = aggregate_persisted_mineru_evidence(
        [{"expected_locators": [{"page": 1}]} for _ in range(4)],
        v4_results,
        v4_store,
    )
    v5_evidence = aggregate_persisted_mineru_evidence(
        [{"expected_locators": [{"page": 1}]} for _ in range(6)],
        v5_results,
        v5_store,
    )
    assert len(v4_transport.names) == 4
    assert len(v5_transport.names) == 6
    assert v4_evidence["matched_locator_count"] == 4
    assert v5_evidence["matched_locator_count"] == 6
    assert (
        int(v4_evidence["matched_locator_count"]) + int(v5_evidence["matched_locator_count"]) == 10
    )


def test_docx_v2_sends_only_remaining_nine_and_combines_to_twenty_locators(
    tmp_path: Path,
) -> None:
    recovered_results, _, recovered_store, _ = _run_scope(
        tmp_path,
        attempt_revision="mineru-docx-recovery:v1-test",
        scope="docx-recovery-test",
        suffix=".docx",
        is_ocr=False,
        count=1,
        max_files=1,
        max_requests=33,
    )
    v2_results, _, v2_store, v2_transport = _run_scope(
        tmp_path,
        attempt_revision="mineru-docx-attempt:v2-test",
        scope="docx-v2-test",
        suffix=".docx",
        is_ocr=False,
        count=9,
        max_files=9,
        max_requests=297,
    )
    recovered = aggregate_persisted_mineru_evidence(
        [{"expected_locators": [{"page": 1}, {"page": 2}]}],
        recovered_results,
        recovered_store,
    )
    v2 = aggregate_persisted_mineru_evidence(
        [{"expected_locators": [{"page": 1}, {"page": 2}]} for _ in range(9)],
        v2_results,
        v2_store,
    )
    assert len(v2_transport.names) == 9
    assert recovered["matched_locator_count"] == 2
    assert v2["matched_locator_count"] == 18
    assert int(recovered["matched_locator_count"]) + int(v2["matched_locator_count"]) == 20
