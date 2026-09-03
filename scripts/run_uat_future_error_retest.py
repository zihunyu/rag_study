"""Plan or execute only a newly approved future error-case structured-claim retest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.provider_http import UatClaimContractHttpTransport  # noqa: E402
from ragkb.application.uat_future_claim_runner import (  # noqa: E402
    FutureErrorCaseRetestV3Runner,
    require_future_case_egress,
)
from ragkb.config import load_env  # noqa: E402
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore  # noqa: E402
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore  # noqa: E402

PLAN = ROOT / "artifacts/final-validation/uat-future-error-retest-v3-plan.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes(), usedforsecurity=False).hexdigest()


def _context() -> tuple[dict[str, object], LocalUatArtifactStore, list[dict[str, object]]]:
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    runner = plan.get("runner")
    if (
        not isinstance(plan, dict)
        or plan.get("revision") != "uat-future-error-retest-plan:v3"
        or plan.get("selected_case_count") != 15
        or not isinstance(plan.get("eligible_case_count"), int)
        or not isinstance(plan.get("blocked_case_count"), int)
        or plan["eligible_case_count"] + plan["blocked_case_count"] != 15
        or plan.get("max_provider_requests") != 15
        or plan.get("per_case_max_requests") != 1
        or plan.get("automatic_retries") != 0
        or not isinstance(runner, dict)
        or runner.get("revision") != "uat-future-error-retest-runner:v3"
        or runner.get("executed") is not False
        or plan.get("historical_artifacts") != "READ_ONLY"
    ):
        raise RuntimeError("UAT_ERROR_RETEST_PLAN_INVALID")
    input_root = artifacts_root / "uat-future-error-retest-v3"
    manifest_path = input_root / "manifest.json"
    if _sha256(manifest_path) != plan.get("input_manifest_sha256"):
        raise RuntimeError("UAT_ERROR_RETEST_INPUT_MANIFEST_HASH_INVALID")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("case_records")
    if not isinstance(records, list) or len(records) != plan["eligible_case_count"]:
        raise RuntimeError("UAT_ERROR_RETEST_INPUT_MANIFEST_INVALID")
    cases = []
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("UAT_ERROR_RETEST_CASE_RECORD_INVALID")
        path = (artifacts_root / str(record.get("case_ref", ""))).resolve()
        if artifacts_root not in path.parents or _sha256(path) != record.get("case_sha256"):
            raise RuntimeError("UAT_ERROR_RETEST_CASE_HASH_INVALID")
        case = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(case, dict) or case.get("test_case_id") != record.get("test_case_id"):
            raise RuntimeError("UAT_ERROR_RETEST_CASE_INVALID")
        cases.append(case)
    return plan, LocalUatArtifactStore(artifacts_root, claim_revision="error-retest-v3"), cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "execute"), nargs="?", default="plan")
    parser.add_argument("--approved", action="store_true")
    args = parser.parse_args()
    plan, store, cases = _context()
    if args.mode == "plan":
        print(
            json.dumps(
                {
                    "revision": plan["revision"],
                    "selected_case_count": plan["selected_case_count"],
                    "eligible_case_count": plan["eligible_case_count"],
                    "blocked_case_count": plan["blocked_case_count"],
                    "max_provider_requests": plan["max_provider_requests"],
                    "automatic_retries": plan["automatic_retries"],
                    "approved_by_user": plan["runner"]["approved_by_user"],
                    "executed": False,
                    "provider_call_count": 0,
                    "content_output": False,
                },
                sort_keys=True,
            )
        )
        return 0
    if not args.approved or plan["runner"].get("approved_by_user") is not True:
        raise RuntimeError("UAT_ERROR_RETEST_EXECUTION_APPROVAL_REQUIRED")
    loaded = load_env(ROOT)
    assert loaded.settings is not None
    require_future_case_egress(
        cases,
        outbound_ai_allowed=loaded.settings.ai_outbound_allowed,
        allowed_classifications=loaded.settings.ai_outbound_allowed_classifications,
        approved_processing_regions=loaded.settings.ai_approved_processing_regions,
    )
    checkpoint = (
        ROOT / "artifacts/final-validation/provider-checkpoints/uat-future-error-retest-v3.json"
    )
    result = FutureErrorCaseRetestV3Runner(
        UatClaimContractHttpTransport(loaded.settings),
        JsonCheckpointStore(checkpoint),
        store,
        external_call_approved=True,
        max_requests=int(plan["max_provider_requests"]),
        timeout_seconds=loaded.settings.llm_timeout_seconds,
    ).run(cases)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
