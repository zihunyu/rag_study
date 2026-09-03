"""Freeze proposal 1 into the approved one-request Reranker v2 diagnostic plan."""

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
from ragkb.evaluation.uat_diagnostic_v2 import (  # noqa: E402
    build_candidate2_diagnostic_v2,
)
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore  # noqa: E402

PROPOSAL_SHA256 = "f281ace99a60efa8ba64c0ead0002e1f9f052993da1126e4b9d3e9c19a2952e7"
FAILURE_REVIEW_SHA256 = "8a330b506ce9b3d4d03ce99c5cf1420c06d346524c0e1ce6bda8f50c8d9eafd3"
RERANKER_V1_SHA256 = "5e2f739a4136afa827ade769b0b4fe1f6715ba278ee2efe7f05457224c5d4df7"
PLAN_PATH = ROOT / "artifacts/final-validation/uat-reranker-diagnostic-v2-plan.json"
V1_CHECKPOINT = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json"
V2_CHECKPOINT = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json"


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
    store = LocalUatArtifactStore(artifacts_root, bundle_revision="v2")
    proposals_path = store.review_root / "candidate2-revision-proposals.json"
    failure_path = store.review_root / "reranker-failure-1.json"
    if _sha256(proposals_path) != PROPOSAL_SHA256:
        raise RuntimeError("UAT_DIAGNOSTIC_V2_PROPOSAL_HASH_MISMATCH")
    if _sha256(failure_path) != FAILURE_REVIEW_SHA256:
        raise RuntimeError("UAT_DIAGNOSTIC_V2_FAILURE_REVIEW_HASH_MISMATCH")
    if _sha256(V1_CHECKPOINT) != RERANKER_V1_SHA256:
        raise RuntimeError("UAT_DIAGNOSTIC_V2_V1_CHECKPOINT_HASH_MISMATCH")
    if V2_CHECKPOINT.exists():
        raise RuntimeError("UAT_RERANKER_V2_CHECKPOINT_ALREADY_EXISTS")
    real_uat_plan = json.loads(
        (ROOT / "artifacts/final-validation/real-uat-plan.json").read_text(encoding="utf-8")
    )
    records = real_uat_plan.get("bundles")
    if not isinstance(records, list) or len(records) != 78:
        raise RuntimeError("UAT_DIAGNOSTIC_V2_ORIGINAL_PLAN_INVALID")
    original_record = records[1]
    if not isinstance(original_record, dict):
        raise RuntimeError("UAT_DIAGNOSTIC_V2_ORIGINAL_RECORD_INVALID")
    original_bundle_path = (artifacts_root / str(original_record["bundle_ref"])).resolve()
    original_bundle_hash = _sha256(original_bundle_path)
    if original_bundle_hash != original_record.get("bundle_sha256"):
        raise RuntimeError("UAT_DIAGNOSTIC_V2_ORIGINAL_BUNDLE_HASH_MISMATCH")
    source_paths = [
        proposals_path,
        failure_path,
        V1_CHECKPOINT,
        ROOT / "artifacts/final-validation/uat-candidates/approved.json",
        ROOT / "artifacts/final-validation/uat-candidates/pending-review.json",
        *sorted(store.bundle_root.glob("*.json")),
    ]
    source_hashes_before = {str(path): _sha256(path) for path in source_paths}
    proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
    failure_review = json.loads(failure_path.read_text(encoding="utf-8"))
    original_bundle = json.loads(original_bundle_path.read_text(encoding="utf-8"))
    revision, bundle = build_candidate2_diagnostic_v2(
        proposals,
        failure_review,
        original_bundle,
        proposal_sha256=PROPOSAL_SHA256,
        failure_review_sha256=FAILURE_REVIEW_SHA256,
        reranker_v1_sha256=RERANKER_V1_SHA256,
        original_bundle_sha256=original_bundle_hash,
    )
    revision_metadata = store.persist_candidate_revision_v2(revision)
    bundle_metadata = store.persist_diagnostic_bundle_v2(str(bundle["candidate_id"]), bundle)
    plan = {
        "revision": "uat-reranker-diagnostic-v2-plan:v1",
        "attempt_revision": "uat-reranker-diagnostic-runner:v2",
        "approved_by_user": True,
        "user_selection": "PROPOSAL_1",
        "authorization_scope": "ONE_RERANKER_V2_DIAGNOSTIC_RETRY_ZERO",
        "runner_review_required": True,
        "executed": False,
        "max_requests": 1,
        "positive_top_k": 2,
        "automatic_retries": 0,
        "llm_request_count": 0,
        "checkpoint_ref": "provider-checkpoints/uat-reranker-v2.json",
        "checkpoint_exists": False,
        "prior_v1_checkpoint_read_only": True,
        "source_hashes": {
            "proposal_sha256": PROPOSAL_SHA256,
            "failure_review_sha256": FAILURE_REVIEW_SHA256,
            "reranker_v1_sha256": RERANKER_V1_SHA256,
            "original_bundle_sha256": original_bundle_hash,
        },
        "source_refs": {
            "proposal_ref": "uat-result-review/candidate2-revision-proposals.json",
            "failure_review_ref": "uat-result-review/reranker-failure-1.json",
            "reranker_v1_ref": "provider-checkpoints/uat-reranker-v1.json",
            "original_bundle_ref": original_record["bundle_ref"],
        },
        "revision_artifact": revision_metadata,
        "bundle": bundle_metadata,
        "document_count": 4,
        "content_output": False,
        "network_call_performed": False,
    }
    _atomic_json(PLAN_PATH, plan)
    source_hashes_after = {str(path): _sha256(path) for path in source_paths}
    if source_hashes_before != source_hashes_after:
        raise RuntimeError("UAT_DIAGNOSTIC_V2_SOURCE_MUTATED")
    print(
        json.dumps(
            {
                "revision": plan["revision"],
                "approved_by_user": True,
                "user_selection": "PROPOSAL_1",
                "max_requests": 1,
                "positive_top_k": 2,
                "automatic_retries": 0,
                "llm_request_count": 0,
                "document_count": 4,
                "bundle_ref": bundle_metadata["bundle_ref"],
                "bundle_sha256": bundle_metadata["bundle_sha256"],
                "revision_ref": revision_metadata["revision_ref"],
                "revision_sha256": revision_metadata["revision_sha256"],
                "source_hashes_unchanged": True,
                "immutable_input_count": len(source_paths),
                "checkpoint_exists": False,
                "executed": False,
                "content_output": False,
                "network_call_performed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
