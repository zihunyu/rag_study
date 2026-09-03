"""Generate controlled local-only revision proposals for failed UAT candidate 2."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.config import load_env  # noqa: E402
from ragkb.evaluation.uat_revision_proposals import (  # noqa: E402
    build_revision_proposals,
)
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore  # noqa: E402

SOURCE_HASH = "8a330b506ce9b3d4d03ce99c5cf1420c06d346524c0e1ce6bda8f50c8d9eafd3"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes(), usedforsecurity=False).hexdigest()


def main() -> int:
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    store = LocalUatArtifactStore(artifacts_root, bundle_revision="v2")
    source_path = store.review_root / "reranker-failure-1.json"
    if _sha256(source_path) != SOURCE_HASH:
        raise RuntimeError("UAT_FAILURE_REVIEW_HASH_MISMATCH")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    documents = source.get("documents")
    if not isinstance(documents, list) or len(documents) != 4:
        raise RuntimeError("UAT_FAILURE_REVIEW_DOCUMENTS_INVALID")
    roles = [document.get("role") for document in documents if isinstance(document, dict)]
    if roles.count("positive") != 1 or roles.count("distractor") != 3:
        raise RuntimeError("UAT_FAILURE_REVIEW_ROLES_INVALID")
    immutable_paths = [
        ROOT / "artifacts/final-validation/uat-candidates/approved.json",
        ROOT / "artifacts/final-validation/uat-candidates/pending-review.json",
        ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json",
        *sorted(store.bundle_root.glob("*.json")),
    ]
    before = {str(path): _sha256(path) for path in immutable_paths}
    proposals = build_revision_proposals(source)
    stored = store.persist_candidate_revision_proposals(2, proposals)
    after = {str(path): _sha256(path) for path in immutable_paths}
    if before != after:
        raise RuntimeError("UAT_IMMUTABLE_INPUT_MUTATED")
    v2_checkpoint = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json"
    v2_checkpoint_exists = v2_checkpoint.exists()
    print(
        json.dumps(
            {
                **stored,
                "proposal_count": 3,
                "source_document_count": 4,
                "source_role_counts": {"positive": 1, "distractor": 3},
                "immutable_input_count": len(immutable_paths),
                "immutable_inputs_unchanged": True,
                "v2_checkpoint_exists": v2_checkpoint_exists,
                "network_call_performed": False,
                "model_call_performed": False,
                "content_output": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
