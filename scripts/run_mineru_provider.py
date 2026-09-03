"""Plan or execute fixed-scope MinerU Precision v4 attempts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.mineru_pool import MinerUTokenPool  # noqa: E402
from ragkb.adapters.provider_http import MinerUHttpTransport  # noqa: E402
from ragkb.application.provider_runners import (  # noqa: E402
    MinerUCapabilityProbe,
    MinerUDocxRecoveryRunner,
    MinerUExecutionRunner,
    require_configured_provider_egress,
)
from ragkb.config import load_env  # noqa: E402
from ragkb.evaluation.format_samples import _resolve  # noqa: E402
from ragkb.evaluation.local_sample_validation import (  # noqa: E402
    _anonymous_id,
    _expected_locator_match,
    aggregate_persisted_mineru_evidence,
)
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore  # noqa: E402
from ragkb.infrastructure.provider_inputs import (  # noqa: E402
    LibreOfficeDocxPdfDeriver,
    SingleFrameTiffPngDeriver,
)
from ragkb.infrastructure.provider_results import LocalProviderResultStore  # noqa: E402

LEGACY_MINERU_CHECKPOINT_SHA256 = "f64a00e00747fa7d9a1f97dde530da07dd63ed060d4f6d6bd810b04c4f9da3f0"
SCAN_V2_CHECKPOINT_SHA256 = "6f3f21ed74c55c4a57afdc4cbf5455b28a470b81198af7fbdf2bad6db39a982a"
SCAN_V3_CHECKPOINT_SHA256 = "a6fe1d1dd651c938d847acbe9181e5291e50dcc79f1906f20ff70eee2b6cc452"
SCAN_V4_CHECKPOINT_SHA256 = "182e4a4811d4708074a4c39fd522d5cf011e8955bef489bd22021a98fa402b07"
SCAN_V5_CHECKPOINT_SHA256 = "71200ca9a76c9655e043886f6e5e996223584e534cadbfca99e3c883fa2678e7"
DOCX_V1_CHECKPOINT_SHA256 = "14df78adbf4b52bbfd69aeafce3b58fb1d79c7d55bf9a343766b9fbbdc07a20e"
DOCX_RECOVERY_CHECKPOINT_NAME = "mineru-docx-recovery-v1.json"
DOCX_RECOVERY_CHECKPOINT_SHA256 = "61dbd8933f56c0f3d82407011d12e745a3a1634c54e30f7fe49f0636d96a14af"
LIBREOFFICE_VERSION = "26.8.0.3"
LIBREOFFICE_CANDIDATES = (
    Path("C:/Program Files/LibreOffice/program/soffice.com"),
    Path("C:/Program Files (x86)/LibreOffice/program/soffice.com"),
)
ATTEMPTS: dict[str, dict[str, object]] = {
    "execute-scan-v2": {
        "attempt_revision": "mineru-scan-attempt:v2",
        "scope": "pdf_scanned_or_image",
        "checkpoint_name": "mineru-scan-attempt-v2.json",
        "expected_locator_count": 10,
        "is_ocr": True,
        "approved_by_user": True,
    },
    "execute-scan-v3": {
        "attempt_revision": "mineru-scan-attempt:v3",
        "scope": "pdf_scanned_or_image",
        "checkpoint_name": "mineru-scan-attempt-v3.json",
        "expected_locator_count": 10,
        "is_ocr": True,
        "approved_by_user": True,
    },
    "execute-scan-v4": {
        "attempt_revision": "mineru-scan-attempt:v4",
        "scope": "pdf_scanned_or_image",
        "checkpoint_name": "mineru-scan-attempt-v4.json",
        "expected_locator_count": 10,
        "is_ocr": True,
        "approved_by_user": True,
    },
    "execute-docx-v1": {
        "attempt_revision": "mineru-docx-attempt:v1",
        "scope": "docx",
        "checkpoint_name": "mineru-docx-attempt-v1.json",
        "expected_locator_count": 20,
        "is_ocr": False,
        "approved_by_user": True,
    },
    "execute-docx-v2": {
        "attempt_revision": "mineru-docx-attempt:v2",
        "scope": "docx",
        "checkpoint_name": "mineru-docx-attempt-v2.json",
        "expected_locator_count": 18,
        "is_ocr": False,
        "approved_by_user": False,
        "position_start": 2,
        "sample_count": 9,
        "max_files": 9,
        "max_requests": 297,
    },
    "execute-docx-pdf-v1": {
        "attempt_revision": "mineru-docx-pdf-attempt:v1",
        "scope": "docx_pdf",
        "source_category": "docx",
        "checkpoint_name": "mineru-docx-pdf-attempt-v1.json",
        "expected_locator_count": 20,
        "is_ocr": False,
        "approved_by_user": True,
        "sample_count": 10,
        "max_files": 10,
        "max_requests": 330,
        "derived_input_required": True,
        "derived_input_prepared": True,
        "converter_revision": "libreoffice-docx-to-pdf:v1",
    },
    "execute-scan-v5": {
        "attempt_revision": "mineru-scan-attempt:v5",
        "scope": "pdf_scanned_or_image",
        "checkpoint_name": "mineru-scan-attempt-v5.json",
        "expected_locator_count": 6,
        "is_ocr": True,
        "approved_by_user": True,
        "position_start": 5,
        "sample_count": 6,
        "max_files": 6,
        "max_requests": 198,
        "derived_input_required": True,
        "derived_input_prepared": True,
        "converter_revision": "single-frame-tiff-to-png:v1",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _settings_and_pool():
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    settings = loaded.settings
    if len(settings.mineru_tokens) != 1:
        raise RuntimeError("MINERU_TOKEN_FORMAT_INVALID")
    raw_token = settings.mineru_tokens[0].get_secret_value()
    if (
        raw_token != raw_token.strip()
        or raw_token.casefold().startswith("bearer ")
        or any(character.isspace() for character in raw_token)
    ):
        raise RuntimeError("MINERU_TOKEN_FORMAT_INVALID")
    pool = MinerUTokenPool(
        settings.mineru_tokens,
        max_concurrency_per_token=settings.mineru_max_concurrency_per_token,
        max_failures=settings.mineru_token_max_failures,
        cooldown_seconds=settings.mineru_token_cooldown_seconds,
        failover_enabled=False,
    )
    return settings, pool


def _attempt_status(attempt: Mapping[str, object], checkpoint_root: Path) -> dict[str, object]:
    path = checkpoint_root / str(attempt["checkpoint_name"])
    if not path.is_file():
        return {
            "executed": False,
            "execution_status": "PLANNED",
            "request_count": 0,
            "completed_files": 0,
            "failed_files": 0,
            "unknown_files": 0,
        }
    loaded = json.loads(path.read_text(encoding="utf-8"))
    raw_namespace = loaded.get("mineru", {}) if isinstance(loaded, dict) else {}
    namespace = raw_namespace if isinstance(raw_namespace, dict) else {}
    records = [
        value for key, value in namespace.items() if key != "_manifest" and isinstance(value, dict)
    ]
    manifest = namespace.get("_manifest", {})
    completed = sum(value.get("state") == "COMPLETED" for value in records)
    failed = sum(value.get("state") == "FAILED" for value in records)
    unknown = sum(value.get("state") == "UNKNOWN_OUTCOME" for value in records)
    required_files = int(attempt.get("sample_count", 10))
    return {
        "executed": bool(records),
        "execution_status": (
            "COMPLETED"
            if completed == required_files
            else "FAILED"
            if failed
            else "UNKNOWN_OUTCOME"
            if unknown
            else "INCOMPLETE"
        ),
        "request_count": int(manifest.get("request_count", 0)),
        "completed_files": completed,
        "failed_files": failed,
        "unknown_files": unknown,
    }


def _recovery_status(checkpoint_root: Path) -> dict[str, object]:
    path = checkpoint_root / DOCX_RECOVERY_CHECKPOINT_NAME
    if not path.is_file():
        return {
            "attempt_revision": "mineru-docx-recovery:v1",
            "checkpoint_ref": f"provider-checkpoints/{DOCX_RECOVERY_CHECKPOINT_NAME}",
            "approved_by_user": False,
            "executed": False,
            "execution_status": "PLANNED",
            "request_count": 0,
            "completed_files": 0,
            "create_count": 0,
            "upload_count": 0,
            "automatic_retries": 0,
        }
    loaded = json.loads(path.read_text(encoding="utf-8"))
    raw_namespace = loaded.get("mineru_recovery", {}) if isinstance(loaded, dict) else {}
    namespace = raw_namespace if isinstance(raw_namespace, dict) else {}
    records = [value for value in namespace.values() if isinstance(value, dict)]
    completed = sum(value.get("state") == "COMPLETED" for value in records)
    failed = sum(value.get("state") == "FAILED" for value in records)
    unknown = sum(value.get("state") == "UNKNOWN_OUTCOME" for value in records)
    return {
        "attempt_revision": "mineru-docx-recovery:v1",
        "checkpoint_ref": f"provider-checkpoints/{DOCX_RECOVERY_CHECKPOINT_NAME}",
        "approved_by_user": True,
        "executed": bool(records),
        "execution_status": (
            "COMPLETED"
            if completed == 1
            else "FAILED"
            if failed
            else "UNKNOWN_OUTCOME"
            if unknown
            else "INCOMPLETE"
        ),
        "request_count": sum(int(value.get("request_count", 0)) for value in records),
        "completed_files": completed,
        "create_count": 0,
        "upload_count": 0,
        "automatic_retries": 0,
    }


def _recovery_locator_gate(checkpoint_root: Path) -> tuple[int, int]:
    recovery_path = checkpoint_root / DOCX_RECOVERY_CHECKPOINT_NAME
    loaded = json.loads(recovery_path.read_text(encoding="utf-8"))
    namespace = loaded.get("mineru_recovery", {})
    completed = [
        value
        for value in namespace.values()
        if isinstance(value, dict) and value.get("state") == "COMPLETED"
    ]
    if len(completed) != 1 or not isinstance(completed[0].get("evidence"), dict):
        return 0, 2
    evidence = completed[0]["evidence"]
    artifact_id = evidence.get("artifact_id")
    sample_id = evidence.get("sample_id")
    if not isinstance(artifact_id, str) or not isinstance(sample_id, str):
        return 0, 2
    loaded_env = load_env(ROOT)
    if loaded_env.settings is None:
        return 0, 2
    artifacts_root = loaded_env.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    nodes = LocalProviderResultStore(artifacts_root).read_mineru_nodes(artifact_id)
    plan = yaml.safe_load(
        (ROOT / "backend/tests/fixtures/manifests/format-samples.yaml").read_text(encoding="utf-8")
    )
    item = next(value for value in plan["collection_plan"] if value["format"] == "docx")
    metadata = yaml.safe_load(
        _resolve(ROOT, str(item["metadata_path"])).read_text(encoding="utf-8")
    )
    first = metadata["samples"][0]
    if _anonymous_id("docx", first) != sample_id:
        return 0, 2
    actual = [
        dict(node["locator"])
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("locator"), dict)
    ]
    expected = [value for value in first["expected_locators"] if isinstance(value, dict)]
    return _expected_locator_match(expected, actual)


def build_attempt_plan(checkpoint_root: Path | None = None) -> dict[str, object]:
    resolved_checkpoint_root = checkpoint_root or (
        ROOT / "artifacts/final-validation/provider-checkpoints"
    )
    attempts = []
    for value in ATTEMPTS.values():
        attempts.append(
            {
                **value,
                **_attempt_status(value, resolved_checkpoint_root),
                "checkpoint_ref": f"provider-checkpoints/{value['checkpoint_name']}",
                "provider_contract": "MINERU_PRECISION_API_V4",
                "max_files": int(value.get("max_files", 10)),
                "max_requests": int(value.get("max_requests", 330)),
                "max_polls_per_file": 30,
                "poll_interval_seconds": 10,
                "automatic_retries": 0,
                "token_failover": False,
                "new_chunks_join_embedding_669": False,
            }
        )
    scan_v4 = next(
        item for item in attempts if item["attempt_revision"] == "mineru-scan-attempt:v4"
    )
    scan_v5 = next(
        item for item in attempts if item["attempt_revision"] == "mineru-scan-attempt:v5"
    )
    docx_v1 = next(
        item for item in attempts if item["attempt_revision"] == "mineru-docx-attempt:v1"
    )
    docx_v2 = next(
        item for item in attempts if item["attempt_revision"] == "mineru-docx-attempt:v2"
    )
    recovery = _recovery_status(resolved_checkpoint_root)
    if recovery["completed_files"] == 1:
        matched, expected = _recovery_locator_gate(resolved_checkpoint_root)
        recovery["expected_locator_count"] = expected
        recovery["matched_locator_count"] = matched
        recovery["locator_gate_passed"] = matched == expected
        if matched != expected:
            recovery["execution_status"] = "COMPLETED_PROVIDER_RESULT_LOCATOR_GATE_FAILED"
    if not docx_v1["executed"] and not (
        scan_v4["completed_files"] == 4 and scan_v5["completed_files"] == 6
    ):
        docx_v1["execution_status"] = "BLOCKED_BY_COMBINED_SCAN_PREREQUISITE"
    if not docx_v2["executed"] and (
        recovery["completed_files"] != 1 or recovery.get("locator_gate_passed") is not True
    ):
        docx_v2["execution_status"] = "BLOCKED_BY_DOCX_RECOVERY_PREREQUISITE"
    return {
        "revision": "mineru-new-attempts-plan:v1",
        "legacy_unknown_checkpoint_ref": "provider-checkpoints/mineru.json",
        "legacy_unknown_checkpoint_sha256": LEGACY_MINERU_CHECKPOINT_SHA256,
        "legacy_checkpoint_mutation_allowed": False,
        "attempts": attempts,
        "docx_recovery": recovery,
        "new_real_call_count": sum(int(item["request_count"]) for item in attempts)
        + int(recovery["request_count"]),
        "secret_values_in_output": False,
        "source_names_in_output": False,
    }


def _preflight_attempt(
    attempt: Mapping[str, object],
    item: Mapping[str, object],
    metadata: Mapping[str, object],
    checkpoint_root: Path | None = None,
) -> tuple[Path, list[Mapping[str, object]]]:
    resolved_checkpoint_root = checkpoint_root or (
        ROOT / "artifacts/final-validation/provider-checkpoints"
    )
    legacy_path = resolved_checkpoint_root / "mineru.json"
    if _sha256(legacy_path) != LEGACY_MINERU_CHECKPOINT_SHA256:
        raise RuntimeError("MINERU_LEGACY_CHECKPOINT_INTEGRITY_MISMATCH")
    scan_v2_path = resolved_checkpoint_root / "mineru-scan-attempt-v2.json"
    if _sha256(scan_v2_path) != SCAN_V2_CHECKPOINT_SHA256:
        raise RuntimeError("MINERU_SCAN_V2_CHECKPOINT_INTEGRITY_MISMATCH")
    scan_v3_path = resolved_checkpoint_root / "mineru-scan-attempt-v3.json"
    if _sha256(scan_v3_path) != SCAN_V3_CHECKPOINT_SHA256:
        raise RuntimeError("MINERU_SCAN_V3_CHECKPOINT_INTEGRITY_MISMATCH")
    scan_v4_path = resolved_checkpoint_root / "mineru-scan-attempt-v4.json"
    if _sha256(scan_v4_path) != SCAN_V4_CHECKPOINT_SHA256:
        raise RuntimeError("MINERU_SCAN_V4_CHECKPOINT_INTEGRITY_MISMATCH")
    if attempt["attempt_revision"] == "mineru-scan-attempt:v5":
        scan_v4_loaded = json.loads(scan_v4_path.read_text(encoding="utf-8"))
        scan_v4_namespace = scan_v4_loaded.get("mineru", {})
        scan_v4_records = [
            value
            for key, value in scan_v4_namespace.items()
            if key != "_manifest" and isinstance(value, dict)
        ]
        completed = sum(value.get("state") == "COMPLETED" for value in scan_v4_records)
        failed = [value for value in scan_v4_records if value.get("state") == "FAILED"]
        if completed != 4 or len(failed) != 1 or failed[0].get("provider_error_code") != "-60002":
            raise RuntimeError("MINERU_SCAN_V5_SCAN_V4_BASELINE_MISMATCH")
    checkpoint_path = resolved_checkpoint_root / str(attempt["checkpoint_name"])
    if checkpoint_path.exists():
        raise RuntimeError("MINERU_NEW_ATTEMPT_CHECKPOINT_NOT_EMPTY")
    if attempt["scope"] in {"docx", "docx_pdf"}:
        scan_v5_path = resolved_checkpoint_root / "mineru-scan-attempt-v5.json"
        if _sha256(scan_v5_path) != SCAN_V5_CHECKPOINT_SHA256:
            raise RuntimeError("MINERU_SCAN_V5_CHECKPOINT_INTEGRITY_MISMATCH")
        docx_v1_path = resolved_checkpoint_root / "mineru-docx-attempt-v1.json"
        if _sha256(docx_v1_path) != DOCX_V1_CHECKPOINT_SHA256:
            raise RuntimeError("MINERU_DOCX_V1_CHECKPOINT_INTEGRITY_MISMATCH")
    if attempt["scope"] == "docx_pdf":
        recovery_path = resolved_checkpoint_root / DOCX_RECOVERY_CHECKPOINT_NAME
        if _sha256(recovery_path) != DOCX_RECOVERY_CHECKPOINT_SHA256:
            raise RuntimeError("MINERU_DOCX_RECOVERY_CHECKPOINT_INTEGRITY_MISMATCH")
    if attempt["scope"] == "docx":
        scan_v4_status = _attempt_status(ATTEMPTS["execute-scan-v4"], resolved_checkpoint_root)
        scan_v5_status = _attempt_status(ATTEMPTS["execute-scan-v5"], resolved_checkpoint_root)
        if not (scan_v4_status["completed_files"] == 4 and scan_v5_status["completed_files"] == 6):
            raise RuntimeError("MINERU_DOCX_COMBINED_SCAN_PREREQUISITE_NOT_MET")
        if attempt["attempt_revision"] == "mineru-docx-attempt:v2":
            recovery_status = _recovery_status(resolved_checkpoint_root)
            if recovery_status["completed_files"] != 1:
                raise RuntimeError("MINERU_DOCX_V2_RECOVERY_PREREQUISITE_NOT_MET")
            matched, expected = _recovery_locator_gate(resolved_checkpoint_root)
            if expected != 2 or matched != expected:
                raise RuntimeError("MINERU_DOCX_V2_RECOVERY_LOCATOR_GATE_NOT_MET")
    directory = _resolve(ROOT, str(item["sample_directory"]))
    raw_samples = metadata.get("samples")
    if not isinstance(raw_samples, list) or len(raw_samples) != 10:
        raise RuntimeError("MINERU_ATTEMPT_SAMPLE_COUNT_MISMATCH")
    all_samples = [sample for sample in raw_samples if isinstance(sample, Mapping)]
    if len(all_samples) != 10:
        raise RuntimeError("MINERU_ATTEMPT_METADATA_INVALID")
    position_start = int(attempt.get("position_start", 1))
    sample_count = int(attempt.get("sample_count", 10))
    samples = all_samples[position_start - 1 : position_start - 1 + sample_count]
    if len(samples) != sample_count:
        raise RuntimeError("MINERU_ATTEMPT_SELECTED_SAMPLE_COUNT_MISMATCH")
    expected_locator_count = sum(
        len(sample.get("expected_locators", []))
        for sample in samples
        if isinstance(sample.get("expected_locators"), list)
    )
    if expected_locator_count != int(attempt["expected_locator_count"]):
        raise RuntimeError("MINERU_ATTEMPT_EXPECTED_LOCATOR_COUNT_MISMATCH")
    for sample in samples:
        source = (directory / str(sample["file"])).resolve()
        if source.parent != directory.resolve() or _sha256(source) != str(sample["sha256"]):
            raise RuntimeError("MINERU_ATTEMPT_SOURCE_SNAPSHOT_MISMATCH")
        if attempt["scope"] == "docx" and source.suffix.casefold() != ".docx":
            raise RuntimeError("MINERU_DOCX_SCOPE_EXTENSION_MISMATCH")
    return directory, samples


def _controlled_temp_root(settings) -> Path:
    value = settings.local_storage_temp_dir
    return value.resolve() if value.is_absolute() else (ROOT / value).resolve()


def _docx_pdf_deriver(settings) -> LibreOfficeDocxPdfDeriver:
    launcher = next((path for path in LIBREOFFICE_CANDIDATES if path.is_file()), None)
    if launcher is None:
        raise RuntimeError("LIBREOFFICE_CONSOLE_LAUNCHER_NOT_FOUND")
    artifacts_root = settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    return LibreOfficeDocxPdfDeriver(
        artifacts_root,
        _controlled_temp_root(settings),
        launcher,
        LIBREOFFICE_VERSION,
    )


def prepare_scan_v5_derived_input() -> dict[str, object]:
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    settings = loaded.settings
    attempt = ATTEMPTS["execute-scan-v5"]
    manifest = yaml.safe_load(
        (ROOT / "backend/tests/fixtures/manifests/format-samples.yaml").read_text(encoding="utf-8")
    )
    item = next(
        value for value in manifest["collection_plan"] if value["format"] == attempt["scope"]
    )
    metadata = yaml.safe_load(
        _resolve(ROOT, str(item["metadata_path"])).read_text(encoding="utf-8")
    )
    directory, samples = _preflight_attempt(attempt, item, metadata)
    first = samples[0]
    derived = SingleFrameTiffPngDeriver(_controlled_temp_root(settings)).derive(
        (directory / str(first["file"])).resolve(),
        _anonymous_id(str(attempt["scope"]), first),
        str(first["sha256"]),
    )
    return {
        "attempt_revision": attempt["attempt_revision"],
        "derived_input_count": 1,
        "converter_revision": derived["converter_revision"],
        "width": derived["width"],
        "height": derived["height"],
        "mode": derived["mode"],
        "frame_count": derived["frame_count"],
        "source_snapshot_preserved": True,
        "anonymous_path": True,
        "network_call_performed": False,
        "source_name_output": False,
        "content_output": False,
    }


def _provider_inputs(
    attempt: Mapping[str, object],
    directory: Path,
    samples: list[Mapping[str, object]],
    settings,
) -> list[tuple[Mapping[str, object], Path, str]]:
    inputs: list[tuple[Mapping[str, object], Path, str]] = []
    deriver = SingleFrameTiffPngDeriver(_controlled_temp_root(settings))
    docx_pdf_deriver = (
        _docx_pdf_deriver(settings)
        if attempt["attempt_revision"] == "mineru-docx-pdf-attempt:v1"
        else None
    )
    for index, sample in enumerate(samples):
        source = (directory / str(sample["file"])).resolve()
        digest = str(sample["sha256"])
        if attempt["attempt_revision"] == "mineru-scan-attempt:v5" and index == 0:
            derived = deriver.load(_anonymous_id(str(attempt["scope"]), sample), digest)
            derived_path = derived.get("derived_path")
            derived_sha256 = derived.get("derived_sha256")
            if not isinstance(derived_path, Path) or not isinstance(derived_sha256, str):
                raise RuntimeError("MINERU_SCAN_V5_DERIVED_INPUT_INVALID")
            source = derived_path
            digest = derived_sha256
        if attempt["attempt_revision"] == "mineru-docx-pdf-attempt:v1":
            assert docx_pdf_deriver is not None
            derived = docx_pdf_deriver.load(_anonymous_id("docx", sample), digest)
            derived_path = derived.get("derived_path")
            derived_sha256 = derived.get("derived_sha256")
            if not isinstance(derived_path, Path) or not isinstance(derived_sha256, str):
                raise RuntimeError("MINERU_DOCX_PDF_DERIVED_INPUT_INVALID")
            source = derived_path
            digest = derived_sha256
        inputs.append((sample, source, digest))
    return inputs


def _run_docx_recovery(settings, pool, signed_hosts: list[str]) -> int:
    checkpoint_root = ROOT / "artifacts/final-validation/provider-checkpoints"
    original_path = checkpoint_root / "mineru-docx-attempt-v1.json"
    if _sha256(original_path) != DOCX_V1_CHECKPOINT_SHA256:
        raise RuntimeError("MINERU_DOCX_V1_CHECKPOINT_INTEGRITY_MISMATCH")
    if _sha256(checkpoint_root / "mineru-scan-attempt-v5.json") != SCAN_V5_CHECKPOINT_SHA256:
        raise RuntimeError("MINERU_SCAN_V5_CHECKPOINT_INTEGRITY_MISMATCH")
    recovery_path = checkpoint_root / DOCX_RECOVERY_CHECKPOINT_NAME
    if recovery_path.exists():
        raise RuntimeError("MINERU_DOCX_RECOVERY_CHECKPOINT_NOT_EMPTY")
    if not (
        _attempt_status(ATTEMPTS["execute-scan-v4"], checkpoint_root)["completed_files"] == 4
        and _attempt_status(ATTEMPTS["execute-scan-v5"], checkpoint_root)["completed_files"] == 6
    ):
        raise RuntimeError("MINERU_DOCX_COMBINED_SCAN_PREREQUISITE_NOT_MET")
    plan = yaml.safe_load(
        (ROOT / "backend/tests/fixtures/manifests/format-samples.yaml").read_text(encoding="utf-8")
    )
    item = next(value for value in plan["collection_plan"] if value["format"] == "docx")
    metadata = yaml.safe_load(
        _resolve(ROOT, str(item["metadata_path"])).read_text(encoding="utf-8")
    )
    first = metadata["samples"][0]
    anonymous_id = _anonymous_id("docx", first)
    original_loaded = json.loads(original_path.read_text(encoding="utf-8"))["mineru"]
    original_failed = original_loaded.get(anonymous_id)
    if not isinstance(original_failed, dict):
        raise RuntimeError("MINERU_DOCX_RECOVERY_SOURCE_INVALID")
    require_configured_provider_egress(
        outbound_ai_allowed=settings.ai_outbound_allowed,
        allowed_classifications=settings.ai_outbound_allowed_classifications,
        approved_processing_regions=settings.ai_approved_processing_regions,
        classifications=[str(first.get("source_classification", ""))],
    )
    artifacts_root = settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    result_store = LocalProviderResultStore(artifacts_root)
    checkpoint_store = JsonCheckpointStore(recovery_path)
    transport = MinerUHttpTransport(
        settings.mineru_base_url,
        allowed_signed_host_suffixes=signed_hosts,
    )
    validator = MinerUExecutionRunner(
        pool,
        transport,
        checkpoint_store,
        result_store,
        external_call_approved=True,
        attempt_revision="mineru-docx-recovery-validator:v1",
        scope="docx",
        locator_policy="office_page_bbox_optional",
    )
    evidence = MinerUDocxRecoveryRunner(
        pool,
        transport,
        checkpoint_store,
        result_store,
        validator.validate_result_zip,
        external_call_approved=True,
        timeout_seconds=settings.mineru_timeout_seconds,
    ).run(original_failed, anonymous_id)
    aggregate = aggregate_persisted_mineru_evidence([first], [evidence], result_store)
    print(
        json.dumps(
            {
                **aggregate,
                "attempt_revision": "mineru-docx-recovery:v1",
                "create_count": 0,
                "upload_count": 0,
                "request_count": evidence["request_count"],
                "automatic_retries": 0,
                "secret_values_in_output": False,
            },
            sort_keys=True,
        )
    )
    return (
        0
        if aggregate["completed_files"] == 1
        and aggregate["matched_locator_count"] == aggregate["expected_locator_count"]
        else 2
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=("plan", "capability", "recover-docx-v1", *ATTEMPTS),
        default="plan",
        nargs="?",
    )
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--signed-host", action="append", default=[])
    args = parser.parse_args()
    if args.mode == "plan":
        print(json.dumps(build_attempt_plan(), sort_keys=True))
        return 0
    if (args.mode in ATTEMPTS or args.mode == "recover-docx-v1") and not args.approved:
        raise RuntimeError("MINERU_EXECUTION_APPROVAL_REQUIRED")
    settings, pool = _settings_and_pool()
    if args.mode == "capability":
        result = MinerUCapabilityProbe(settings.mineru_base_url).probe()
        print(json.dumps(result, sort_keys=True))
        return 0
    if MinerUCapabilityProbe(settings.mineru_base_url).probe()["status"] != (
        "DOCX_DOCUMENTED_SUPPORTED"
    ):
        raise RuntimeError("MINERU_OFFICIAL_V4_CONFIGURATION_REQUIRED")
    if args.mode == "recover-docx-v1":
        return _run_docx_recovery(settings, pool, args.signed_host)
    attempt = ATTEMPTS[args.mode]
    plan = yaml.safe_load(
        (ROOT / "backend/tests/fixtures/manifests/format-samples.yaml").read_text(encoding="utf-8")
    )
    source_category = str(attempt.get("source_category", attempt["scope"]))
    item = next(value for value in plan["collection_plan"] if value["format"] == source_category)
    metadata = yaml.safe_load(
        _resolve(ROOT, str(item["metadata_path"])).read_text(encoding="utf-8")
    )
    directory, samples = _preflight_attempt(attempt, item, metadata)
    require_configured_provider_egress(
        outbound_ai_allowed=settings.ai_outbound_allowed,
        allowed_classifications=settings.ai_outbound_allowed_classifications,
        approved_processing_regions=settings.ai_approved_processing_regions,
        classifications=[str(sample.get("source_classification", "")) for sample in samples],
    )
    artifacts_root = settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    result_store = LocalProviderResultStore(artifacts_root)
    provider_inputs = _provider_inputs(attempt, directory, samples, settings)
    checkpoint_path = (
        ROOT / "artifacts/final-validation/provider-checkpoints" / str(attempt["checkpoint_name"])
    )
    runner = MinerUExecutionRunner(
        pool,
        MinerUHttpTransport(
            settings.mineru_base_url,
            allowed_signed_host_suffixes=args.signed_host,
        ),
        JsonCheckpointStore(checkpoint_path),
        result_store,
        external_call_approved=True,
        attempt_revision=str(attempt["attempt_revision"]),
        scope=str(attempt["scope"]),
        locator_policy=(
            "office_page_bbox_optional" if attempt["scope"] == "docx" else "strict_page_bbox"
        ),
        max_files=int(attempt.get("max_files", 10)),
        max_requests=int(attempt.get("max_requests", 330)),
        max_polls_per_file=30,
        poll_interval_seconds=10,
        timeout_seconds=settings.mineru_timeout_seconds,
        model_version=settings.mineru_model_version,
        enable_table=settings.mineru_enable_table,
        enable_formula=settings.mineru_enable_formula,
    )
    results: list[dict[str, object]] = []
    for sample, source, digest in provider_inputs:
        results.append(
            runner.run_file(
                source,
                _anonymous_id(str(attempt["scope"]), sample),
                digest,
                is_ocr=bool(attempt["is_ocr"]),
            )
        )
    aggregate = aggregate_persisted_mineru_evidence(samples, results, result_store)
    print(
        json.dumps(
            {
                **aggregate,
                "attempt_revision": attempt["attempt_revision"],
                "scope": attempt["scope"],
                "expected_locator_target": attempt["expected_locator_count"],
                "max_files": int(attempt.get("max_files", 10)),
                "max_requests": int(attempt.get("max_requests", 330)),
                "automatic_retries": 0,
                "secret_values_in_output": False,
            },
            sort_keys=True,
        )
    )
    expected_files = int(attempt.get("sample_count", 10))
    return (
        0
        if aggregate["completed_files"] == expected_files
        and aggregate["matched_locator_count"] == aggregate["expected_locator_count"]
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
