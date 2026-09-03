"""Create one controlled local review artifact from the immutable v1 failure."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.application.uat_provider_runners import _validated_bundles  # noqa: E402
from ragkb.config import load_env  # noqa: E402
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore  # noqa: E402

PLAN_PATH = ROOT / "artifacts/final-validation/real-uat-plan.json"
CHECKPOINT_PATH = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json"
SUMMARY_PATH = ROOT / "artifacts/final-validation/real-uat-results-summary.json"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload, usedforsecurity=False).hexdigest()


def main() -> int:
    checkpoint_before = CHECKPOINT_PATH.read_bytes()
    checkpoint_hash = _sha256(checkpoint_before)
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    if checkpoint_hash != summary.get("reranker", {}).get("checkpoint_sha256"):
        raise RuntimeError("UAT_V1_CHECKPOINT_HASH_MISMATCH")
    checkpoint = json.loads(checkpoint_before)
    namespace = checkpoint.get("uat_reranker")
    if not isinstance(namespace, dict):
        raise RuntimeError("UAT_V1_CHECKPOINT_INVALID")
    manifest = namespace.get("_manifest")
    records = [
        value for key, value in namespace.items() if key != "_manifest" and isinstance(value, dict)
    ]
    failed = [value for value in records if value.get("state") == "FAILED"]
    completed = [value for value in records if value.get("state") == "COMPLETED"]
    if (
        not isinstance(manifest, dict)
        or manifest.get("request_count") != 2
        or len(completed) != 1
        or len(failed) != 1
        or failed[0].get("error_code") != "UAT_RERANKER_POSITIVE_NOT_IN_TOP_K"
        or any(
            key in failed[0]
            for key in ("ranked_evidence_ids", "positive_rank", "response_index_count")
        )
    ):
        raise RuntimeError("UAT_V1_FAILURE_EVIDENCE_INVALID")
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    store = LocalUatArtifactStore(artifacts_root, bundle_revision="v2")
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    plan_records = plan.get("bundles")
    if not isinstance(plan_records, list) or len(plan_records) != 78:
        raise RuntimeError("UAT_PLAN_INVALID")
    bundles = [store.read_bundle(str(record["candidate_id"])) for record in plan_records]
    _validated_bundles(bundles)
    failed_candidate_id = str(failed[0].get("candidate_id"))
    if failed_candidate_id != str(plan_records[1].get("candidate_id")):
        raise RuntimeError("UAT_FAILURE_NOT_SECOND_CANDIDATE")
    bundle = bundles[1]
    documents = bundle.get("documents")
    if not isinstance(documents, list) or len(documents) != 4:
        raise RuntimeError("UAT_FAILURE_BUNDLE_INVALID")
    review = {
        "revision": "uat-reranker-failure-review:v1",
        "source_checkpoint_ref": "provider-checkpoints/uat-reranker-v1.json",
        "source_checkpoint_sha256": checkpoint_hash,
        "request_ordinal": 2,
        "candidate_id": failed_candidate_id,
        "question": bundle["question"],
        "documents": [
            {
                "evidence_id": document["evidence_id"],
                "role": document["role"],
                "locator": document["locator"],
                "content": document["content"],
                "content_sha256": document["content_sha256"],
            }
            for document in documents
        ],
        "expected_positive_evidence_id": bundle["expected_positive_evidence_id"],
        "error_code": "UAT_RERANKER_POSITIVE_NOT_IN_TOP_K",
        "provider_order_unavailable": True,
        "positive_rank_unknown": True,
        "manual_review_status": "PENDING_LOCAL_MANUAL_REVIEW",
    }
    stored = store.persist_reranker_failure_review("reranker-failure-1", review)
    if _sha256(CHECKPOINT_PATH.read_bytes()) != checkpoint_hash:
        raise RuntimeError("UAT_V1_CHECKPOINT_MUTATED")
    print(
        json.dumps(
            {
                **stored,
                "document_count": 4,
                "provider_order_unavailable": True,
                "positive_rank_unknown": True,
                "source_checkpoint_unchanged": True,
                "content_output": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
