"""Plan or execute a bounded structured-claim acceptance run from an explicit case file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.provider_http import UatClaimContractHttpTransport  # noqa: E402
from ragkb.application.uat_claim_runner import UatClaimRunner, require_case_egress  # noqa: E402
from ragkb.config import build_env_report, load_env  # noqa: E402
from ragkb.contracts.provider_execution import ProviderExecutionError  # noqa: E402
from ragkb.infrastructure.claim_artifacts import ClaimArtifactStore  # noqa: E402
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore  # noqa: E402


class _PlanTransport:
    real_network = False

    def generate_claims(self, contract, idempotency_key, timeout_seconds):
        raise RuntimeError("CLAIM_PLAN_CANNOT_EXECUTE")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True, help="JSON array of acceptance cases")
    parser.add_argument("--run-id", required=True, help="Isolated run name; reuse only to resume")
    parser.add_argument("--max-requests", type=int, required=True)
    parser.add_argument(
        "--execute", action="store_true", help="Default is validation and plan only"
    )
    parser.add_argument(
        "--approved", action="store_true", help="Explicit approval for provider calls"
    )
    parser.add_argument("--artifacts", type=Path, default=ROOT / "artifacts/claim-acceptance")
    args = parser.parse_args(argv)
    if args.execute and not args.approved:
        print(json.dumps({"status": "CLAIM_EXECUTION_APPROVAL_REQUIRED", "provider_call_count": 0}))
        return 2
    try:
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
        if not isinstance(cases, list) or any(not isinstance(case, dict) for case in cases):
            raise ValueError("CLAIM_CASES_MUST_BE_ARRAY_OF_OBJECTS")
        artifacts = ClaimArtifactStore(args.artifacts, run_id=args.run_id)
        checkpoints = JsonCheckpointStore(args.artifacts / "checkpoints" / f"{args.run_id}.json")
        runner = UatClaimRunner(
            _PlanTransport(),
            checkpoints,
            artifacts,
            external_call_approved=False,
            max_requests=args.max_requests,
            run_id=args.run_id,
        )
        plan = runner.plan(cases)
        if not args.execute:
            print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
            return 0
        loaded = load_env(ROOT)
        report = build_env_report(loaded, "G4")
        settings = loaded.settings
        if settings is None or not report["summary"]["gate_ready"]:
            print(json.dumps({"status": "G4_CONFIG_NOT_READY", "provider_call_count": 0}))
            return 3
        if not settings.real_provider_calls_enabled:
            print(json.dumps({"status": "REAL_PROVIDER_CALLS_DISABLED", "provider_call_count": 0}))
            return 3
        require_case_egress(
            cases,
            outbound_ai_allowed=settings.ai_outbound_allowed,
            allowed_classifications=settings.ai_outbound_allowed_classifications,
            approved_processing_regions=settings.ai_approved_processing_regions,
        )
        runner = UatClaimRunner(
            UatClaimContractHttpTransport(settings),
            checkpoints,
            artifacts,
            external_call_approved=True,
            max_requests=args.max_requests,
            run_id=args.run_id,
        )
        print(json.dumps(runner.run(cases), ensure_ascii=False, sort_keys=True))
        return 0
    except ProviderExecutionError as error:
        print(json.dumps({"status": error.code, "outcome_unknown": error.outcome_unknown}))
        return 4
    except (ValueError, OSError):
        print(json.dumps({"status": "CLAIM_INPUT_OR_ARTIFACT_INVALID"}))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
