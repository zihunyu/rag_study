"""Plan or execute the fixed two-stage locator-grounded real UAT."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.provider_http import (  # noqa: E402
    UatLlmHttpTransport,
    UatRerankerHttpTransport,
)
from ragkb.application.provider_runners import (  # noqa: E402
    require_configured_provider_egress,
)
from ragkb.application.uat_provider_runners import (  # noqa: E402
    UatLlmExecutionRunner,
    UatRerankerExecutionRunner,
)
from ragkb.config import load_env  # noqa: E402
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore  # noqa: E402
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore  # noqa: E402

PLAN_PATH = ROOT / "artifacts/final-validation/real-uat-plan.json"
RERANKER_CHECKPOINT = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json"
LLM_CHECKPOINT = ROOT / "artifacts/final-validation/provider-checkpoints/uat-llm-v1.json"


def _context():
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    settings = loaded.settings
    artifacts_root = settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    store = LocalUatArtifactStore(artifacts_root, bundle_revision="v2")
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    records = plan.get("bundles")
    if not isinstance(records, list) or len(records) != 78:
        raise RuntimeError("UAT_PLAN_BUNDLES_INVALID")
    bundles = [store.read_bundle(str(record["candidate_id"])) for record in records]
    classifications = [str(bundle["source_classification"]) for bundle in bundles]
    require_configured_provider_egress(
        outbound_ai_allowed=settings.ai_outbound_allowed,
        allowed_classifications=settings.ai_outbound_allowed_classifications,
        approved_processing_regions=settings.ai_approved_processing_regions,
        classifications=classifications,
    )
    return settings, store, plan, bundles


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "reranker", "llm"), default="plan", nargs="?")
    parser.add_argument("--approved", action="store_true")
    args = parser.parse_args()
    if args.mode == "plan":
        plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "revision": plan["revision"],
                    "bundle_count": plan["bundle_count"],
                    "documents_per_bundle": plan["documents_per_bundle"],
                    "reranker": plan["reranker"],
                    "candidate2_reranker_diagnostic_v2": plan["candidate2_reranker_diagnostic_v2"],
                    "remaining_reranker_continuation_v3": plan[
                        "remaining_reranker_continuation_v3"
                    ],
                    "systematic_revision_v4": plan["systematic_revision_v4"],
                    "systematic_revision_v5": plan["systematic_revision_v5"],
                    "llm": plan["llm"],
                    "total_model_request_budget": plan["total_model_request_budget"],
                    "conditional_user_authorization_satisfied": plan[
                        "conditional_user_authorization_satisfied"
                    ],
                    "runner_review_required": True,
                    "executed": False,
                    "query_embedding_request_count": 0,
                    "zilliz_request_count": 0,
                    "content_output": False,
                    "source_names_output": False,
                },
                sort_keys=True,
            )
        )
        return 0
    if not args.approved:
        raise RuntimeError("REAL_UAT_EXECUTION_APPROVAL_REQUIRED")
    settings, store, _, bundles = _context()
    if args.mode == "reranker":
        if RERANKER_CHECKPOINT.exists():
            raise RuntimeError("UAT_RERANKER_CHECKPOINT_NOT_EMPTY")
        result = UatRerankerExecutionRunner(
            UatRerankerHttpTransport(settings),
            JsonCheckpointStore(RERANKER_CHECKPOINT),
            external_call_approved=True,
            max_requests=78,
            positive_top_k=2,
            timeout_seconds=settings.reranker_timeout_seconds,
        ).run(bundles)
    else:
        if LLM_CHECKPOINT.exists():
            raise RuntimeError("UAT_LLM_CHECKPOINT_NOT_EMPTY")
        result = UatLlmExecutionRunner(
            UatLlmHttpTransport(settings),
            JsonCheckpointStore(RERANKER_CHECKPOINT),
            JsonCheckpointStore(LLM_CHECKPOINT),
            store,
            external_call_approved=True,
            max_requests=78,
            reranker_top_k=2,
            timeout_seconds=settings.llm_timeout_seconds,
        ).run(bundles)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
