"""Generate the two-term systematic v5 review and unapproved Reranker plan."""

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
from ragkb.evaluation.uat_systematic_revision_v5 import (  # noqa: E402
    build_systematic_revision_v5,
)
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore  # noqa: E402

V1 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json"
V2 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json"
V3 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v3.json"
V4 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v4.json"
V5 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v5.json"
PLAN_PATH = ROOT / "artifacts/final-validation/uat-systematic-revision-v5-plan.json"
EXPECTED = {
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


def main() -> int:
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    store = LocalUatArtifactStore(artifacts_root, bundle_revision="v2")
    checkpoint_paths = {"v1": V1, "v2": V2, "v3": V3, "v4": V4}
    actual_hashes = {key: _sha256(path) for key, path in checkpoint_paths.items()}
    if actual_hashes != EXPECTED:
        raise RuntimeError("UAT_SYSTEMATIC_V5_CHECKPOINT_HASH_MISMATCH")
    if V5.exists():
        raise RuntimeError("UAT_RERANKER_V5_CHECKPOINT_MUST_NOT_EXIST")
    namespace = json.loads(V4.read_text(encoding="utf-8")).get("uat_reranker_v4", {})
    records = [
        value for key, value in namespace.items() if key != "_manifest" and isinstance(value, dict)
    ]
    failed = [value for value in records if value.get("state") == "FAILED"]
    if (
        namespace.get("_manifest", {}).get("request_count") != 37
        or sum(value.get("state") == "COMPLETED" for value in records) != 36
        or len(failed) != 1
        or sum(value.get("state") == "UNKNOWN_OUTCOME" for value in records) != 0
        or failed[0].get("candidate_id") != "8b6b08e289b402b1f741"
        or failed[0].get("positive_rank") != 3
        or failed[0].get("response_index_count") != 4
        or len(failed[0].get("ranked_evidence_ids", [])) != 4
    ):
        raise RuntimeError("UAT_SYSTEMATIC_V5_V4_RESULT_INVALID")
    v4_plan = json.loads(
        (ROOT / "artifacts/final-validation/uat-systematic-v4-execution-plan.json").read_text(
            encoding="utf-8"
        )
    )
    selected_records = v4_plan.get("selected_bundles")
    if not isinstance(selected_records, list) or len(selected_records) != 78:
        raise RuntimeError("UAT_SYSTEMATIC_V5_SOURCE_PLAN_INVALID")
    passed_records = selected_records[:39]
    source_records = selected_records[39:]
    source_bundles = []
    for record in source_records:
        if not isinstance(record, dict):
            raise RuntimeError("UAT_SYSTEMATIC_V5_SOURCE_RECORD_INVALID")
        path = (artifacts_root / str(record["bundle_ref"])).resolve()
        if artifacts_root not in path.parents or _sha256(path) != record.get("bundle_sha256"):
            raise RuntimeError("UAT_SYSTEMATIC_V5_SOURCE_BUNDLE_HASH_MISMATCH")
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(bundle, dict):
            raise RuntimeError("UAT_SYSTEMATIC_V5_SOURCE_BUNDLE_INVALID")
        source_bundles.append(bundle)
    immutable_paths = [
        *checkpoint_paths.values(),
        ROOT / "artifacts/final-validation/uat-candidates/approved.json",
        ROOT / "artifacts/final-validation/uat-candidates/pending-review.json",
        *sorted(store.bundle_root.glob("*.json")),
        *sorted(store.review_root.glob("*.json")),
        *sorted(store.diagnostic_bundle_root.glob("*.json")),
        *sorted(store.systematic_revision_root.rglob("*.json")),
    ]
    before = {str(path): _sha256(path) for path in immutable_paths}
    review, revised_bundles, manifest_base = build_systematic_revision_v5(
        source_bundles, source_records
    )
    manifest_base.update(
        source_checkpoint_hashes=actual_hashes,
        passed_source_counts={"v1": 1, "v2": 1, "v3": 1, "v4": 36},
        v4_failure={
            "candidate_id": failed[0]["candidate_id"],
            "positive_rank": 3,
            "response_index_count": 4,
            "error_code": failed[0]["error_code"],
        },
    )
    stored = store.persist_systematic_revision_v5(review, revised_bundles, manifest_base)
    after = {str(path): _sha256(path) for path in immutable_paths}
    if before != after:
        raise RuntimeError("UAT_SYSTEMATIC_V5_SOURCE_MUTATED")
    revisions = review["revisions"]
    assert isinstance(revisions, list)
    revised_records = [
        {
            "position": item["position"],
            "original_candidate_id": item["original_candidate_id"],
            "source_revision_candidate_id": item["source_revision_candidate_id"],
            "candidate_id": item["revision_candidate_id"],
            "bundle_ref": item["revision_bundle_ref"],
            "bundle_sha256": item["revision_bundle_sha256"],
            "source_checkpoint": "v5",
        }
        for item in revisions
        if isinstance(item, dict)
    ]
    final_records = [*passed_records, *revised_records]
    if len(final_records) != 78:
        raise RuntimeError("UAT_SYSTEMATIC_V5_FINAL_SET_INVALID")
    plan = {
        "revision": "uat-systematic-revision-plan:v5",
        "review_status": "PENDING_USER_REVIEW",
        "review_artifact": stored,
        "passed_count": 39,
        "pending_revision_count": 39,
        "selected_bundle_count": 78,
        "selected_bundles": final_records,
        "selected_bundle_snapshot_sha256": _canonical_hash(final_records),
        "reranker_v5": {
            "checkpoint_ref": "provider-checkpoints/uat-reranker-v5.json",
            "candidate_count": 39,
            "max_requests": 39,
            "positive_top_k": 2,
            "automatic_retries": 0,
            "approved_by_user": False,
            "runner_review_required": True,
            "executed": False,
        },
        "llm": {
            "candidate_count": 78,
            "prerequisite": "COMBINED_39_EXISTING_PLUS_V5_39_GATE_78_OF_78",
            "approved_for_revised_set": False,
            "executed": False,
        },
        "source_checkpoint_hashes": actual_hashes,
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
                "passed_count": 39,
                "pending_revision_count": 39,
                "selected_bundle_count": 78,
                "category_counts": review["category_counts"],
                "review_ref": stored["review_ref"],
                "review_sha256": stored["review_sha256"],
                "manifest_ref": stored["manifest_ref"],
                "manifest_sha256": stored["manifest_sha256"],
                "revision_bundle_count": stored["bundle_count"],
                "reranker_v5_max_requests": 39,
                "reranker_v5_approved": False,
                "llm_approved_for_revised_set": False,
                "old_input_count": len(immutable_paths),
                "old_inputs_unchanged": True,
                "v5_checkpoint_exists": False,
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
