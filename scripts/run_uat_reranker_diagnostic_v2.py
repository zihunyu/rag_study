"""Plan or execute the one-request candidate 2 Reranker v2 diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.provider_http import UatRerankerHttpTransport  # noqa: E402
from ragkb.application.provider_runners import (  # noqa: E402
    require_configured_provider_egress,
)
from ragkb.application.uat_provider_runners import (  # noqa: E402
    UatRerankerDiagnosticV2Runner,
)
from ragkb.config import EnvSettings, load_env  # noqa: E402
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore  # noqa: E402
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore  # noqa: E402

PLAN_PATH = ROOT / "artifacts/final-validation/uat-reranker-diagnostic-v2-plan.json"
CHECKPOINT_PATH = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes(), usedforsecurity=False).hexdigest()


def _safe_artifact_path(root: Path, reference: object) -> Path:
    path = (root / str(reference)).resolve()
    if root != path and root not in path.parents:
        raise RuntimeError("UAT_RERANKER_V2_ARTIFACT_REF_INVALID")
    return path


def _context() -> tuple[EnvSettings, dict[str, object]]:
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    required = ("RERANKER_BASE_URL", "RERANKER_API_KEY", "RERANKER_MODEL")
    if not all(loaded.configured.get(key, False) for key in required):
        raise RuntimeError("UAT_RERANKER_V2_REQUIRED_CONFIG_MISSING")
    settings = loaded.settings
    artifacts_root = settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    store = LocalUatArtifactStore(artifacts_root, bundle_revision="v2")
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    expected = {
        "revision": "uat-reranker-diagnostic-v2-plan:v1",
        "attempt_revision": "uat-reranker-diagnostic-runner:v2",
        "approved_by_user": True,
        "user_selection": "PROPOSAL_1",
        "authorization_scope": "ONE_RERANKER_V2_DIAGNOSTIC_RETRY_ZERO",
        "executed": False,
        "max_requests": 1,
        "positive_top_k": 2,
        "automatic_retries": 0,
        "llm_request_count": 0,
        "checkpoint_exists": False,
    }
    if any(plan.get(key) != value for key, value in expected.items()):
        raise RuntimeError("UAT_RERANKER_V2_PLAN_INVALID")
    source_hashes = plan.get("source_hashes")
    source_refs = plan.get("source_refs")
    bundle_record = plan.get("bundle")
    revision_record = plan.get("revision_artifact")
    if not all(
        isinstance(value, dict)
        for value in (source_hashes, source_refs, bundle_record, revision_record)
    ):
        raise RuntimeError("UAT_RERANKER_V2_PLAN_ARTIFACTS_INVALID")
    assert isinstance(source_hashes, dict)
    assert isinstance(source_refs, dict)
    assert isinstance(bundle_record, dict)
    assert isinstance(revision_record, dict)
    source_paths = {
        "proposal_sha256": store.review_root / "candidate2-revision-proposals.json",
        "failure_review_sha256": store.review_root / "reranker-failure-1.json",
        "reranker_v1_sha256": (
            ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json"
        ),
        "original_bundle_sha256": _safe_artifact_path(
            artifacts_root, source_refs["original_bundle_ref"]
        ),
    }
    if any(_sha256(path) != source_hashes.get(key) for key, path in source_paths.items()):
        raise RuntimeError("UAT_RERANKER_V2_SOURCE_HASH_MISMATCH")
    revision_path = _safe_artifact_path(artifacts_root, revision_record["revision_ref"])
    bundle_path = _safe_artifact_path(artifacts_root, bundle_record["bundle_ref"])
    if _sha256(revision_path) != revision_record.get("revision_sha256") or _sha256(
        bundle_path
    ) != bundle_record.get("bundle_sha256"):
        raise RuntimeError("UAT_RERANKER_V2_ARTIFACT_HASH_MISMATCH")
    bundle = store.read_diagnostic_bundle_v2(str(bundle_record["candidate_id"]))
    if bundle.get("manifest") != json.loads(revision_path.read_text(encoding="utf-8")).get(
        "manifest"
    ):
        raise RuntimeError("UAT_RERANKER_V2_MANIFEST_MISMATCH")
    require_configured_provider_egress(
        outbound_ai_allowed=settings.ai_outbound_allowed,
        allowed_classifications=settings.ai_outbound_allowed_classifications,
        approved_processing_regions=settings.ai_approved_processing_regions,
        classifications=[str(bundle["source_classification"])],
    )
    return settings, bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "execute"), default="plan", nargs="?")
    parser.add_argument("--approved", action="store_true")
    args = parser.parse_args()
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    if args.mode == "plan":
        print(
            json.dumps(
                {
                    "revision": plan["revision"],
                    "approved_by_user": plan["approved_by_user"],
                    "user_selection": plan["user_selection"],
                    "max_requests": plan["max_requests"],
                    "positive_top_k": plan["positive_top_k"],
                    "automatic_retries": plan["automatic_retries"],
                    "llm_request_count": 0,
                    "checkpoint_ref": plan["checkpoint_ref"],
                    "checkpoint_exists": CHECKPOINT_PATH.exists(),
                    "runner_review_required": plan["runner_review_required"],
                    "executed": plan["executed"],
                    "content_output": False,
                    "network_call_performed": plan["network_call_performed"],
                },
                sort_keys=True,
            )
        )
        return 0
    if not args.approved:
        raise RuntimeError("UAT_RERANKER_V2_EXECUTION_APPROVAL_REQUIRED")
    if CHECKPOINT_PATH.exists():
        raise RuntimeError("UAT_RERANKER_V2_CHECKPOINT_ALREADY_EXISTS")
    settings, bundle = _context()
    result = UatRerankerDiagnosticV2Runner(
        UatRerankerHttpTransport(settings),
        JsonCheckpointStore(CHECKPOINT_PATH),
        external_call_approved=True,
        max_requests=1,
        positive_top_k=2,
        timeout_seconds=settings.reranker_timeout_seconds,
    ).run(bundle)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
