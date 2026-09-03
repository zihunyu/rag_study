"""Freeze user-approved systematic v4 Reranker and conditional LLM v3 execution."""

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

SOURCE_PLAN = ROOT / "artifacts/final-validation/uat-systematic-revision-v4-plan.json"
OUTPUT_PLAN = ROOT / "artifacts/final-validation/uat-systematic-v4-execution-plan.json"
V4 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v4.json"
LLM_V3 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-llm-v3.json"
COMBINED = ROOT / "artifacts/final-validation/uat-combined-reranker-gate-v4.json"
EXPECTED = {
    "review": "b3be4dd16601548ee27dc9551461f5fe87759f3721383595cb5abdc16e42d670",
    "manifest": "30b996d1f0f7ab9b5e5dd2b0bb6ce23c2845a1cdca5f4434a5fa1f064f4b56af",
    "v1": "5e2f739a4136afa827ade769b0b4fe1f6715ba278ee2efe7f05457224c5d4df7",
    "v2": "7fccd3f4aa9eff6fbe0128753bdf9e51b01cb0da932248838044542b9e031eb1",
    "v3": "72a2fdf766891a8414bc6a77848828d41d3bf96274fcb82094b55f209ce4b30e",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes(), usedforsecurity=False).hexdigest()


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


def main() -> int:
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    paths = {
        "review": artifacts_root / "uat-systematic-revision-v4/approved-review.json",
        "manifest": artifacts_root / "uat-systematic-revision-v4/manifest.json",
        "v1": ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json",
        "v2": ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json",
        "v3": ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v3.json",
    }
    if {key: _sha256(path) for key, path in paths.items()} != EXPECTED:
        raise RuntimeError("UAT_SYSTEMATIC_V4_FROZEN_HASH_MISMATCH")
    result_root = artifacts_root / "uat-results/v3"
    if (
        V4.exists()
        or LLM_V3.exists()
        or COMBINED.exists()
        or (result_root.exists() and any(result_root.iterdir()))
    ):
        raise RuntimeError("UAT_SYSTEMATIC_V4_NEW_ARTIFACTS_NOT_EMPTY")
    source = json.loads(SOURCE_PLAN.read_text(encoding="utf-8"))
    records = source.get("selected_bundles")
    if (
        source.get("revision") != "uat-systematic-revision-plan:v4"
        or source.get("review_status") != "PENDING_USER_REVIEW"
        or not isinstance(records, list)
        or len(records) != 78
        or source.get("pending_revision_count") != 75
    ):
        raise RuntimeError("UAT_SYSTEMATIC_V4_SOURCE_PLAN_INVALID")
    for position, record in enumerate(records, start=1):
        if not isinstance(record, dict) or record.get("position") != position:
            raise RuntimeError("UAT_SYSTEMATIC_V4_BUNDLE_RECORD_INVALID")
        path = (artifacts_root / str(record["bundle_ref"])).resolve()
        if artifacts_root not in path.parents or _sha256(path) != record.get("bundle_sha256"):
            raise RuntimeError("UAT_SYSTEMATIC_V4_BUNDLE_HASH_MISMATCH")
    plan = {
        "revision": "uat-systematic-v4-execution-plan:v1",
        "approved_by_user": True,
        "authorization_scope": "SYSTEMATIC_V4_75_RERANKER_THEN_CONDITIONAL_78_LLM_RETRY_ZERO",
        "source_review_sha256": EXPECTED["review"],
        "source_manifest_sha256": EXPECTED["manifest"],
        "source_checkpoint_hashes": {
            "v1": EXPECTED["v1"],
            "v2": EXPECTED["v2"],
            "v3": EXPECTED["v3"],
        },
        "selected_bundle_count": 78,
        "selected_bundles": records,
        "selected_bundle_snapshot_sha256": source["selected_bundle_snapshot_sha256"],
        "reranker_v4": {
            "checkpoint_ref": "provider-checkpoints/uat-reranker-v4.json",
            "candidate_count": 75,
            "max_requests": 75,
            "positive_top_k": 2,
            "automatic_retries": 0,
            "approved_by_user": True,
            "runner_review_required": True,
            "executed": False,
        },
        "combined_gate_v4": {
            "artifact_ref": "final-validation/uat-combined-reranker-gate-v4.json",
            "required_count": 78,
            "sources": {"v1": 1, "v2": 1, "v3": 1, "v4": 75},
            "ready": False,
        },
        "llm_v3": {
            "checkpoint_ref": "provider-checkpoints/uat-llm-v3.json",
            "result_ref": "uat-results/v3",
            "candidate_count": 78,
            "max_requests": 78,
            "automatic_retries": 0,
            "prerequisite": "COMBINED_RERANKER_GATE_V4_78_OF_78",
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
    _atomic_json(OUTPUT_PLAN, plan)
    print(
        json.dumps(
            {
                "revision": plan["revision"],
                "approved_by_user": True,
                "selected_bundle_count": 78,
                "reranker_v4_count": 75,
                "reranker_v4_max_requests": 75,
                "llm_v3_conditional_count": 78,
                "llm_v3_max_requests": 78,
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
