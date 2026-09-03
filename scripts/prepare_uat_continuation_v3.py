"""Freeze the authorized remaining-76 Reranker and conditional-78 LLM plan."""

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

PLAN_PATH = ROOT / "artifacts/final-validation/uat-continuation-v3-plan.json"
RERANKER_V1 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json"
RERANKER_V2 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json"
RERANKER_V3 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v3.json"
LLM_V2 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-llm-v2.json"
V1_SHA256 = "5e2f739a4136afa827ade769b0b4fe1f6715ba278ee2efe7f05457224c5d4df7"
V2_SHA256 = "7fccd3f4aa9eff6fbe0128753bdf9e51b01cb0da932248838044542b9e031eb1"


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


def main() -> int:
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    if _sha256(RERANKER_V1) != V1_SHA256 or _sha256(RERANKER_V2) != V2_SHA256:
        raise RuntimeError("UAT_CONTINUATION_PRIOR_CHECKPOINT_HASH_MISMATCH")
    result_v2_root = artifacts_root / "uat-results/v2"
    if (
        RERANKER_V3.exists()
        or LLM_V2.exists()
        or (result_v2_root.exists() and any(result_v2_root.iterdir()))
    ):
        raise RuntimeError("UAT_CONTINUATION_NEW_EXECUTION_ARTIFACTS_NOT_EMPTY")
    real_plan = json.loads(
        (ROOT / "artifacts/final-validation/real-uat-plan.json").read_text(encoding="utf-8")
    )
    original_records = real_plan.get("bundles")
    diagnostic_plan = json.loads(
        (ROOT / "artifacts/final-validation/uat-reranker-diagnostic-v2-plan.json").read_text(
            encoding="utf-8"
        )
    )
    diagnostic_record = diagnostic_plan.get("bundle")
    if (
        not isinstance(original_records, list)
        or len(original_records) != 78
        or not isinstance(diagnostic_record, dict)
        or diagnostic_plan.get("executed") is not True
        or diagnostic_plan.get("execution_result", {}).get("gate_passed") is not True
    ):
        raise RuntimeError("UAT_CONTINUATION_SOURCE_PLAN_INVALID")
    selected_records = [original_records[0], diagnostic_record, *original_records[2:]]
    safe_records = []
    for position, record in enumerate(selected_records, start=1):
        if not isinstance(record, dict):
            raise RuntimeError("UAT_CONTINUATION_BUNDLE_RECORD_INVALID")
        reference = str(record["bundle_ref"])
        bundle_path = (artifacts_root / reference).resolve()
        if artifacts_root not in bundle_path.parents or _sha256(bundle_path) != record.get(
            "bundle_sha256"
        ):
            raise RuntimeError("UAT_CONTINUATION_BUNDLE_HASH_MISMATCH")
        safe_records.append(
            {
                "position": position,
                "candidate_id": record["candidate_id"],
                "bundle_ref": reference,
                "bundle_sha256": record["bundle_sha256"],
                "source_checkpoint": "v1" if position == 1 else "v2" if position == 2 else "v3",
            }
        )
    plan = {
        "revision": "uat-continuation-plan:v3",
        "approved_by_user": True,
        "authorization_scope": ("REMAINING_76_RERANKER_THEN_CONDITIONAL_78_LLM_RETRY_ZERO"),
        "selected_bundle_count": 78,
        "selected_bundles": safe_records,
        "selected_bundle_snapshot_hash": _canonical_hash(safe_records),
        "reranker_v3": {
            "checkpoint_ref": "provider-checkpoints/uat-reranker-v3.json",
            "remaining_candidate_count": 76,
            "max_requests": 76,
            "positive_top_k": 2,
            "automatic_retries": 0,
            "global_failure_policy": "STOP_ALL_AND_DO_NOT_START_LLM",
            "approved_by_user": True,
            "runner_review_required": True,
            "executed": False,
        },
        "combined_gate": {
            "required_count": 78,
            "sources": {"v1": 1, "v2": 1, "v3": 76},
            "artifact_ref": "final-validation/uat-combined-reranker-gate-v3.json",
            "ready": False,
        },
        "llm_v2": {
            "checkpoint_ref": "provider-checkpoints/uat-llm-v2.json",
            "result_ref": "uat-results/v2",
            "candidate_count": 78,
            "max_requests": 78,
            "automatic_retries": 0,
            "prerequisite": "COMBINED_RERANKER_GATE_78_OF_78",
            "approved_by_user": True,
            "runner_review_required": True,
            "executed": False,
            "user_result_review_required": True,
        },
        "source_checkpoint_hashes": {"v1": V1_SHA256, "v2": V2_SHA256},
        "query_embedding_request_count": 0,
        "zilliz_request_count": 0,
        "executed": False,
        "real_uat_passed": False,
        "content_output": False,
        "network_call_performed": False,
    }
    _atomic_json(PLAN_PATH, plan)
    print(
        json.dumps(
            {
                "revision": plan["revision"],
                "approved_by_user": True,
                "selected_bundle_count": 78,
                "remaining_reranker_count": 76,
                "reranker_max_requests": 76,
                "llm_conditional_count": 78,
                "llm_max_requests": 78,
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
