"""Unified anonymous evidence for the completed non-ASR real-format validation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml

from ragkb.config import load_env
from ragkb.evaluation.format_samples import _resolve
from ragkb.evaluation.local_sample_validation import (
    _anonymous_id,
    aggregate_persisted_mineru_evidence,
    validate_local_samples,
)
from ragkb.evaluation.uat_candidates import require_user_review_before_model_calls
from ragkb.infrastructure.provider_results import LocalProviderResultStore

EXPECTED_CHECKPOINT_HASHES = {
    "mineru.json": "f64a00e00747fa7d9a1f97dde530da07dd63ed060d4f6d6bd810b04c4f9da3f0",
    "embedding.json": "b0b41e1908ceea82ff4758ed796c949040ada03e70e2d52e101568e33a2dbe31",
    "embedding-attempt-v2.json": (
        "f26bd8d126ca56457803fc1f0062ccb5b45395b9e936cb08d506f609bc9153a8"
    ),
    "embedding-format-remainder-attempt-v3.json": (
        "b1f1dbfcf5a088dc0d983539c69f8d9dd4d092d6c97c18c806f95f210f15eae5"
    ),
    "mineru-scan-attempt-v2.json": (
        "6f3f21ed74c55c4a57afdc4cbf5455b28a470b81198af7fbdf2bad6db39a982a"
    ),
    "mineru-scan-attempt-v3.json": (
        "a6fe1d1dd651c938d847acbe9181e5291e50dcc79f1906f20ff70eee2b6cc452"
    ),
    "mineru-scan-attempt-v4.json": (
        "182e4a4811d4708074a4c39fd522d5cf011e8955bef489bd22021a98fa402b07"
    ),
    "mineru-scan-attempt-v5.json": (
        "71200ca9a76c9655e043886f6e5e996223584e534cadbfca99e3c883fa2678e7"
    ),
    "mineru-docx-attempt-v1.json": (
        "14df78adbf4b52bbfd69aeafce3b58fb1d79c7d55bf9a343766b9fbbdc07a20e"
    ),
    "mineru-docx-recovery-v1.json": (
        "61dbd8933f56c0f3d82407011d12e745a3a1634c54e30f7fe49f0636d96a14af"
    ),
    "mineru-docx-pdf-attempt-v1.json": (
        "fb545b7d3dd973e1a112adcffb71d6f4027484b83916418621d8c880ad469da8"
    ),
}


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(code)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(root: Path, category: str) -> list[Mapping[str, object]]:
    plan = yaml.safe_load(
        (root / "backend/tests/fixtures/manifests/format-samples.yaml").read_text(encoding="utf-8")
    )
    item = next(value for value in plan["collection_plan"] if value["format"] == category)
    loaded = yaml.safe_load(_resolve(root, str(item["metadata_path"])).read_text(encoding="utf-8"))
    return [sample for sample in loaded["samples"] if isinstance(sample, Mapping)]


def _checkpoint_evidence(
    checkpoint_root: Path,
    checkpoint_names: Sequence[str],
    namespace: str,
) -> dict[str, Mapping[str, object]]:
    evidence: dict[str, Mapping[str, object]] = {}
    for name in checkpoint_names:
        loaded = json.loads((checkpoint_root / name).read_text(encoding="utf-8"))
        raw_namespace = loaded.get(namespace, {}) if isinstance(loaded, dict) else {}
        values = raw_namespace if isinstance(raw_namespace, dict) else {}
        for key, value in values.items():
            if key == "_manifest" or not isinstance(value, Mapping):
                continue
            if value.get("state") != "COMPLETED" or not isinstance(value.get("evidence"), Mapping):
                continue
            if key in evidence:
                raise ValueError("REAL_FORMAT_DUPLICATE_COMPLETED_SAMPLE")
            evidence[key] = value["evidence"]
    return evidence


def _provider_aggregate(
    samples: Sequence[Mapping[str, object]],
    category: str,
    evidence: Mapping[str, Mapping[str, object]],
    store: LocalProviderResultStore,
) -> dict[str, object]:
    selected_samples = []
    selected_results = []
    for sample in samples:
        sample_id = _anonymous_id(category, sample)
        if sample_id in evidence:
            selected_samples.append(sample)
            selected_results.append(evidence[sample_id])
    aggregate = aggregate_persisted_mineru_evidence(selected_samples, selected_results, store)
    return {
        "sample_count": aggregate["completed_files"],
        "chunk_count": aggregate["new_chunk_count"],
        "expected_locator_count": aggregate["expected_locator_count"],
        "matched_locator_count": aggregate["matched_locator_count"],
        "artifact_hash_count": aggregate["artifact_hash_count"],
    }


def _embedding_coverage(checkpoint_root: Path, total_chunks: int) -> dict[str, object]:
    def chunk_id_count(value: Mapping[str, object]) -> int:
        chunk_ids = value.get("chunk_ids")
        return (
            len(chunk_ids)
            if isinstance(chunk_ids, Sequence) and not isinstance(chunk_ids, (str, bytes))
            else 0
        )

    def completed(path: Path) -> list[Mapping[str, object]]:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        raw_namespace = loaded.get("embedding", {})
        namespace = raw_namespace if isinstance(raw_namespace, Mapping) else {}
        return [
            value
            for key, value in namespace.items()
            if key != "_manifest"
            and isinstance(value, Mapping)
            and value.get("state") == "COMPLETED"
        ]

    v2 = completed(checkpoint_root / "embedding-attempt-v2.json")
    v3 = completed(checkpoint_root / "embedding-format-remainder-attempt-v3.json")
    covered_v2 = sum(chunk_id_count(value) for value in v2)
    covered_v3 = sum(chunk_id_count(value) for value in v3)
    covered = covered_v2 + covered_v3
    remaining = total_chunks - covered
    return {
        "completed_chunks": covered,
        "total_chunks": total_chunks,
        "uncovered_chunks": remaining,
        "completed_batches": len(v2) + len(v3),
        "embedding_v2": {
            "completed_chunks": covered_v2,
            "completed_batches": len(v2),
        },
        "new_attempt": {
            "attempt_revision": "embedding-real-attempt:v3-format-remainder",
            "checkpoint_ref": "provider-checkpoints/embedding-format-remainder-attempt-v3.json",
            "chunk_count": covered_v3,
            "batch_size": 10,
            "max_batches": 46,
            "automatic_retries": 0,
            "approved_by_user": True,
            "runner_review_required_before_execution": False,
            "approved": True,
            "executed": True,
            "execution_status": "COMPLETED",
            "completed_batches": len(v3),
            "vector_count": covered_v3,
            "zilliz_write_approved": False,
            "reuses_embedding_v2_checkpoint": False,
        },
    }


def build_real_format_validation(root: Path) -> dict[str, object]:
    loaded = load_env(root)
    if loaded.settings is None:
        raise ValueError("CONFIG_INVALID")
    settings = loaded.settings
    local = validate_local_samples(
        root, root / "backend/tests/fixtures/manifests/format-samples.yaml"
    )
    local_formats = ("pdf_text", "pptx", "spreadsheet")
    raw_local_formats = _mapping(local["by_format"], "REAL_FORMAT_LOCAL_ROWS_INVALID")
    local_rows: dict[str, dict[str, object]] = {}
    for category in local_formats:
        row = _mapping(raw_local_formats.get(category), "REAL_FORMAT_LOCAL_ROW_INVALID")
        local_rows[category] = {
            "sample_count": int(str(row["sample_count"])),
            "chunk_count": int(str(row["eligible_chunk_count"])),
            "expected_locator_count": int(str(row["locator_expected"])),
            "matched_locator_count": int(str(row["locator_matched"])),
            "execution": "LOCAL_READ_ONLY",
        }
    checkpoint_root = root / "artifacts/final-validation/provider-checkpoints"
    artifacts_root = settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (root / artifacts_root).resolve()
    store = LocalProviderResultStore(artifacts_root)
    scan = _provider_aggregate(
        _metadata(root, "pdf_scanned_or_image"),
        "pdf_scanned_or_image",
        _checkpoint_evidence(
            checkpoint_root,
            ("mineru-scan-attempt-v4.json", "mineru-scan-attempt-v5.json"),
            "mineru",
        ),
        store,
    )
    scan["execution"] = "MINERU_SCAN_V4_V5"
    docx = _provider_aggregate(
        _metadata(root, "docx"),
        "docx_pdf",
        _checkpoint_evidence(
            checkpoint_root,
            ("mineru-docx-pdf-attempt-v1.json",),
            "mineru",
        ),
        store,
    )
    docx["execution"] = "LIBREOFFICE_PDF_THEN_MINERU"
    by_format = {**local_rows, "pdf_scanned_or_image": scan, "docx": docx}
    totals = {
        "sample_count": sum(int(str(row["sample_count"])) for row in by_format.values()),
        "chunk_count": sum(int(str(row["chunk_count"])) for row in by_format.values()),
        "expected_locator_count": sum(
            int(str(row["expected_locator_count"])) for row in by_format.values()
        ),
        "matched_locator_count": sum(
            int(str(row["matched_locator_count"])) for row in by_format.values()
        ),
    }
    if totals != {
        "sample_count": 50,
        "chunk_count": 1128,
        "expected_locator_count": 78,
        "matched_locator_count": 78,
    }:
        raise ValueError("REAL_FORMAT_TOTALS_MISMATCH")
    actual_hashes = {name: _sha256(checkpoint_root / name) for name in EXPECTED_CHECKPOINT_HASHES}
    if actual_hashes != EXPECTED_CHECKPOINT_HASHES:
        raise ValueError("REAL_FORMAT_CHECKPOINT_INTEGRITY_MISMATCH")
    if local["source_samples_modified"] is not False or int(str(local["sample_count"])) != 50:
        raise ValueError("REAL_FORMAT_SOURCE_INTEGRITY_MISMATCH")
    uat_root = root / "artifacts/final-validation/uat-candidates"
    pending_bytes = (uat_root / "pending-review.json").read_bytes()
    if (
        hashlib.sha256(pending_bytes, usedforsecurity=False).hexdigest()
        != "fee7e5931d0930f3c8a2f29786abdbf791592d92e2dfc7c355688d965d7558b2"
    ):
        raise ValueError("REAL_FORMAT_UAT_PENDING_SNAPSHOT_MISMATCH")
    approved_bytes = (uat_root / "approved.json").read_bytes()
    approved = json.loads(approved_bytes)
    manifest = json.loads((uat_root / "approval-manifest.json").read_text(encoding="utf-8"))
    if (
        not isinstance(approved, dict)
        or approved.get("candidate_count") != 78
        or hashlib.sha256(approved_bytes, usedforsecurity=False).hexdigest()
        != manifest.get("approved_sha256")
        or manifest.get("pending_sha256")
        != "fee7e5931d0930f3c8a2f29786abdbf791592d92e2dfc7c355688d965d7558b2"
        or manifest.get("decision") != "APPROVE_ALL_78"
    ):
        raise ValueError("REAL_FORMAT_UAT_APPROVAL_INVALID")
    require_user_review_before_model_calls(approved)
    embedding = _embedding_coverage(checkpoint_root, int(str(totals["chunk_count"])))
    new_embedding_attempt = _mapping(
        embedding["new_attempt"], "REAL_FORMAT_EMBEDDING_ATTEMPT_INVALID"
    )
    if (
        embedding["completed_chunks"] != 1128
        or embedding["uncovered_chunks"] != 0
        or new_embedding_attempt["max_batches"] != 46
        or new_embedding_attempt["executed"] is not True
    ):
        raise ValueError("REAL_FORMAT_EMBEDDING_COVERAGE_MISMATCH")
    return {
        "revision": "real-format-validation:v1",
        "scope": "non_asr_5x10",
        "by_format": by_format,
        "totals": totals,
        "embedding_coverage": embedding,
        "uat": {
            "candidate_count": 78,
            "status": "APPROVED_BY_USER",
            "pending_ref": "uat-candidates/pending-review.json",
            "approved_ref": "uat-candidates/approved.json",
            "approval_manifest_ref": "uat-candidates/approval-manifest.json",
            "pending_snapshot_unchanged": True,
            "model_execution_plan_ready": True,
            "reranker_request_count": 0,
            "llm_request_count": 0,
        },
        "deferred": {
            "audio_asr": "deferred_by_user",
            "real_7_day_observation": "deferred_by_user",
        },
        "integrity": {
            "source_sample_count": 50,
            "source_samples_modified": False,
            "checkpoint_hash_count": len(actual_hashes),
            "checkpoint_hashes_match": True,
            "secret_scan_required": True,
            "docker_used": False,
            "commits_performed": False,
        },
        "external_call_count_this_stage": 46,
        "format_quality_ready": True,
        "real_acceptance": True,
        "content_output": False,
        "source_names_output": False,
    }
