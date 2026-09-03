"""Generate all 75 pending UAT question revisions and an unapproved v4 plan."""

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
from ragkb.evaluation.uat_systematic_revision import (  # noqa: E402
    build_systematic_revision_v4,
)
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore  # noqa: E402

V1 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json"
V2 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json"
V3 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v3.json"
V4 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v4.json"
PLAN_PATH = ROOT / "artifacts/final-validation/uat-systematic-revision-v4-plan.json"
EXPECTED = {
    "v1": "5e2f739a4136afa827ade769b0b4fe1f6715ba278ee2efe7f05457224c5d4df7",
    "v2": "7fccd3f4aa9eff6fbe0128753bdf9e51b01cb0da932248838044542b9e031eb1",
    "v3": "72a2fdf766891a8414bc6a77848828d41d3bf96274fcb82094b55f209ce4b30e",
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


def main() -> int:
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    store = LocalUatArtifactStore(artifacts_root, bundle_revision="v2")
    actual_checkpoint_hashes = {"v1": _sha256(V1), "v2": _sha256(V2), "v3": _sha256(V3)}
    if actual_checkpoint_hashes != EXPECTED:
        raise RuntimeError("UAT_SYSTEMATIC_REVISION_CHECKPOINT_HASH_MISMATCH")
    if V4.exists():
        raise RuntimeError("UAT_RERANKER_V4_CHECKPOINT_MUST_NOT_EXIST")
    v3_loaded = json.loads(V3.read_text(encoding="utf-8")).get("uat_reranker_v3", {})
    v3_records = [
        value for key, value in v3_loaded.items() if key != "_manifest" and isinstance(value, dict)
    ]
    if (
        v3_loaded.get("_manifest", {}).get("request_count") != 2
        or sum(value.get("state") == "COMPLETED" for value in v3_records) != 1
        or sum(value.get("state") == "FAILED" for value in v3_records) != 1
        or sum(value.get("state") == "UNKNOWN_OUTCOME" for value in v3_records) != 0
    ):
        raise RuntimeError("UAT_SYSTEMATIC_REVISION_V3_RESULT_INVALID")
    failed = next(value for value in v3_records if value.get("state") == "FAILED")
    if (
        failed.get("candidate_id") != "5501ee1caa0b00088c93"
        or failed.get("error_code") != "UAT_RERANKER_V3_POSITIVE_NOT_IN_TOP_K"
        or failed.get("positive_rank") != 4
        or failed.get("response_index_count") != 4
        or len(failed.get("ranked_evidence_ids", [])) != 4
    ):
        raise RuntimeError("UAT_SYSTEMATIC_REVISION_V3_FAILURE_INVALID")
    continuation_plan = json.loads(
        (ROOT / "artifacts/final-validation/uat-continuation-v3-plan.json").read_text(
            encoding="utf-8"
        )
    )
    selected_records = continuation_plan.get("selected_bundles")
    if not isinstance(selected_records, list) or len(selected_records) != 78:
        raise RuntimeError("UAT_SYSTEMATIC_REVISION_SOURCE_PLAN_INVALID")
    source_records = selected_records[3:]
    source_bundles = []
    for record in source_records:
        if not isinstance(record, dict):
            raise RuntimeError("UAT_SYSTEMATIC_REVISION_SOURCE_RECORD_INVALID")
        path = (artifacts_root / str(record["bundle_ref"])).resolve()
        if artifacts_root not in path.parents or _sha256(path) != record.get("bundle_sha256"):
            raise RuntimeError("UAT_SYSTEMATIC_REVISION_SOURCE_BUNDLE_HASH_MISMATCH")
        loaded_bundle = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded_bundle, dict):
            raise RuntimeError("UAT_SYSTEMATIC_REVISION_SOURCE_BUNDLE_INVALID")
        source_bundles.append(loaded_bundle)
    immutable_paths = [
        V1,
        V2,
        V3,
        ROOT / "artifacts/final-validation/uat-candidates/approved.json",
        ROOT / "artifacts/final-validation/uat-candidates/pending-review.json",
        *sorted(store.bundle_root.glob("*.json")),
        store.review_root / "reranker-failure-1.json",
        store.review_root / "candidate2-revision-proposals.json",
        store.review_root / "candidate2-revision-v2.json",
        *sorted(store.diagnostic_bundle_root.glob("*.json")),
    ]
    before = {str(path): _sha256(path) for path in immutable_paths}
    review, revised_bundles, manifest_base = build_systematic_revision_v4(
        source_bundles, source_records
    )
    manifest_base.update(
        source_checkpoint_hashes=actual_checkpoint_hashes,
        passed_source_counts={"v1": 1, "v2": 1, "v3": 1},
        v3_failure={
            "candidate_id": failed["candidate_id"],
            "positive_rank": 4,
            "response_index_count": 4,
            "error_code": failed["error_code"],
        },
    )
    stored = store.persist_systematic_revision_v4(review, revised_bundles, manifest_base)
    after = {str(path): _sha256(path) for path in immutable_paths}
    if before != after:
        raise RuntimeError("UAT_SYSTEMATIC_REVISION_SOURCE_MUTATED")
    revisions = review["revisions"]
    assert isinstance(revisions, list)
    passed_records = selected_records[:3]
    revised_records = [
        {
            "position": item["position"],
            "original_candidate_id": item["original_candidate_id"],
            "candidate_id": item["revision_candidate_id"],
            "bundle_ref": item["revision_bundle_ref"],
            "bundle_sha256": item["revision_bundle_sha256"],
            "source_checkpoint": "v4",
        }
        for item in revisions
        if isinstance(item, dict)
    ]
    final_records = [*passed_records, *revised_records]
    if len(final_records) != 78:
        raise RuntimeError("UAT_SYSTEMATIC_REVISION_FINAL_SET_INVALID")
    plan = {
        "revision": "uat-systematic-revision-plan:v4",
        "review_status": "PENDING_USER_REVIEW",
        "review_artifact": stored,
        "passed_count": 3,
        "pending_revision_count": 75,
        "selected_bundle_count": 78,
        "selected_bundles": final_records,
        "selected_bundle_snapshot_sha256": _canonical_hash(final_records),
        "reranker_v4": {
            "checkpoint_ref": "provider-checkpoints/uat-reranker-v4.json",
            "candidate_count": 75,
            "max_requests": 75,
            "positive_top_k": 2,
            "automatic_retries": 0,
            "approved_by_user": False,
            "runner_review_required": True,
            "executed": False,
        },
        "llm": {
            "candidate_count": 78,
            "prerequisite": "COMBINED_3_EXISTING_PLUS_V4_75_GATE_78_OF_78",
            "approved_for_revised_set": False,
            "executed": False,
        },
        "source_checkpoint_hashes": actual_checkpoint_hashes,
        "model_call_performed": False,
        "network_call_performed": False,
        "content_output": False,
        "real_uat_passed": False,
    }
    _atomic_json(PLAN_PATH, plan)
    print(
        json.dumps(
            {
                "revision": plan["revision"],
                "passed_count": 3,
                "pending_revision_count": 75,
                "selected_bundle_count": 78,
                "category_counts": review["category_counts"],
                "review_ref": stored["review_ref"],
                "review_sha256": stored["review_sha256"],
                "manifest_ref": stored["manifest_ref"],
                "manifest_sha256": stored["manifest_sha256"],
                "revision_bundle_count": stored["bundle_count"],
                "reranker_v4_max_requests": 75,
                "reranker_v4_approved": False,
                "llm_approved_for_revised_set": False,
                "old_input_count": len(immutable_paths),
                "old_inputs_unchanged": True,
                "v4_checkpoint_exists": False,
                "model_call_performed": False,
                "network_call_performed": False,
                "content_output": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
