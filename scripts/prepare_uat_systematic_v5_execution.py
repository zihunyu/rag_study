"""Freeze the user-approved v5 Reranker and conditional LLM v4 execution plan."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.config import load_env  # noqa: E402

SOURCE_PLAN = ROOT / "artifacts/final-validation/uat-systematic-revision-v5-plan.json"
OUTPUT_PLAN = ROOT / "artifacts/final-validation/uat-systematic-v5-execution-plan.json"
V1 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json"
V2 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json"
V3 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v3.json"
V4 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v4.json"
V5 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v5.json"
LLM_V4 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-llm-v4.json"
COMBINED = ROOT / "artifacts/final-validation/uat-combined-reranker-gate-v5.json"

EXPECTED = {
    "review": "6ecd5ef50fae97805aa35496dfbf795dfe6038e01ac876bf1cb10714954e68b2",
    "manifest": "c6af47f4c19b704d57d80ad5c17dc95cd23f4c6a58080cb8eff14975266eeb80",
    "v1": "5e2f739a4136afa827ade769b0b4fe1f6715ba278ee2efe7f05457224c5d4df7",
    "v2": "7fccd3f4aa9eff6fbe0128753bdf9e51b01cb0da932248838044542b9e031eb1",
    "v3": "72a2fdf766891a8414bc6a77848828d41d3bf96274fcb82094b55f209ce4b30e",
    "v4": "e024ec8029370125116db5180f5ea0f12d699c388aa76ba3e4ded5cd294b901a",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes(), usedforsecurity=False).hexdigest()


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode(),
        usedforsecurity=False,
    ).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _expected_source(position: int) -> str:
    if position == 1:
        return "v1"
    if position == 2:
        return "v2"
    if position == 3:
        return "v3"
    return "v4" if position <= 39 else "v5"


def _assert_content_free(plan: dict[str, object]) -> None:
    serialized = json.dumps(plan, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if any(
        f'"{key}"' in serialized for key in ("question", "content", "answer", "api_key", "base_url")
    ):
        raise RuntimeError("UAT_SYSTEMATIC_V5_EXECUTION_PLAN_SENSITIVE_CONTENT")


def main() -> int:
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    paths = {
        "review": artifacts_root / "uat-systematic-revision-v5/approved-review.json",
        "manifest": artifacts_root / "uat-systematic-revision-v5/manifest.json",
        "v1": V1,
        "v2": V2,
        "v3": V3,
        "v4": V4,
    }
    if {key: _sha256(path) for key, path in paths.items()} != EXPECTED:
        raise RuntimeError("UAT_SYSTEMATIC_V5_FROZEN_HASH_MISMATCH")
    result_root = artifacts_root / "uat-results/v4"
    if (
        V5.exists()
        or LLM_V4.exists()
        or COMBINED.exists()
        or (result_root.exists() and any(result_root.iterdir()))
    ):
        raise RuntimeError("UAT_SYSTEMATIC_V5_NEW_ARTIFACTS_NOT_EMPTY")
    source = json.loads(SOURCE_PLAN.read_text(encoding="utf-8"))
    records = source.get("selected_bundles")
    if (
        source.get("revision") != "uat-systematic-revision-plan:v5"
        or source.get("review_status") != "PENDING_USER_REVIEW"
        or source.get("passed_count") != 39
        or source.get("pending_revision_count") != 39
        or source.get("selected_bundle_count") != 78
        or not isinstance(records, list)
        or len(records) != 78
        or source.get("selected_bundle_snapshot_sha256") != _canonical_hash(records)
        or source.get("source_checkpoint_hashes")
        != {key: EXPECTED[key] for key in ("v1", "v2", "v3", "v4")}
    ):
        raise RuntimeError("UAT_SYSTEMATIC_V5_SOURCE_PLAN_INVALID")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    if (
        manifest.get("revision") != "uat-systematic-revision-manifest:v5"
        or manifest.get("candidate_count") != 39
        or manifest.get("bundle_count") != 39
        or manifest.get("status") != "PENDING_USER_REVIEW"
        or manifest.get("review_sha256") != EXPECTED["review"]
        or manifest.get("source_checkpoint_hashes")
        != {key: EXPECTED[key] for key in ("v1", "v2", "v3", "v4")}
        or manifest.get("passed_source_counts") != {"v1": 1, "v2": 1, "v3": 1, "v4": 36}
        or not isinstance(manifest.get("bundle_snapshot_sha256"), str)
    ):
        raise RuntimeError("UAT_SYSTEMATIC_V5_MANIFEST_INVALID")
    for position, record in enumerate(records, start=1):
        if (
            not isinstance(record, dict)
            or record.get("position") != position
            or record.get("source_checkpoint") != _expected_source(position)
        ):
            raise RuntimeError("UAT_SYSTEMATIC_V5_BUNDLE_RECORD_INVALID")
        if position >= 40 and not isinstance(record.get("source_revision_candidate_id"), str):
            raise RuntimeError("UAT_SYSTEMATIC_V5_REVISION_PROVENANCE_INVALID")
        path = (artifacts_root / str(record.get("bundle_ref", ""))).resolve()
        if artifacts_root not in path.parents or _sha256(path) != record.get("bundle_sha256"):
            raise RuntimeError("UAT_SYSTEMATIC_V5_BUNDLE_HASH_MISMATCH")
    plan = {
        "revision": "uat-systematic-v5-execution-plan:v1",
        "approved_by_user": True,
        "authorization_scope": "SYSTEMATIC_V5_39_RERANKER_THEN_CONDITIONAL_78_LLM_RETRY_ZERO",
        "source_revision_plan_sha256": _sha256(SOURCE_PLAN),
        "source_review_sha256": EXPECTED["review"],
        "source_manifest_sha256": EXPECTED["manifest"],
        "source_checkpoint_hashes": {key: EXPECTED[key] for key in ("v1", "v2", "v3", "v4")},
        "v5_revision_bundle_snapshot_sha256": manifest["bundle_snapshot_sha256"],
        "selected_bundle_count": 78,
        "selected_bundles": records,
        "selected_bundle_snapshot_sha256": source["selected_bundle_snapshot_sha256"],
        "reranker_v5": {
            "checkpoint_ref": "provider-checkpoints/uat-reranker-v5.json",
            "candidate_count": 39,
            "max_requests": 39,
            "positive_top_k": 2,
            "automatic_retries": 0,
            "approved_by_user": True,
            "runner_review_required": True,
            "executed": False,
        },
        "combined_gate_v5": {
            "artifact_ref": "final-validation/uat-combined-reranker-gate-v5.json",
            "required_count": 78,
            "sources": {"v1": 1, "v2": 1, "v3": 1, "v4": 36, "v5": 39},
            "ready": False,
        },
        "llm_v4": {
            "checkpoint_ref": "provider-checkpoints/uat-llm-v4.json",
            "result_ref": "uat-results/v4",
            "candidate_count": 78,
            "max_requests": 78,
            "reranker_top_k": 2,
            "automatic_retries": 0,
            "prerequisite": "COMBINED_RERANKER_GATE_V5_78_OF_78",
            "approved_by_user": True,
            "runner_review_required": True,
            "executed": False,
            "user_result_review_required": True,
        },
        "executed": False,
        "real_uat_passed": False,
        "network_call_performed": False,
        "content_output": False,
    }
    _assert_content_free(plan)
    _atomic_json(OUTPUT_PLAN, plan)
    print(
        json.dumps(
            {
                "revision": plan["revision"],
                "approved_by_user": True,
                "existing_passed_count": 39,
                "reranker_v5_count": 39,
                "reranker_v5_max_requests": 39,
                "combined_gate_required_count": 78,
                "llm_v4_conditional_count": 78,
                "llm_v4_max_requests": 78,
                "automatic_retries": 0,
                "new_checkpoint_count": 0,
                "executed": False,
                "network_call_performed": False,
                "content_output": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
