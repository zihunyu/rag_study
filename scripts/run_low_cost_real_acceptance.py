"""Execute the explicitly approved 10-case, 60-call real acceptance workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.config import build_env_report, load_env  # noqa: E402
from ragkb.evaluation.low_cost_acceptance import (  # noqa: E402
    LowCostRealAcceptanceRunner,
    load_gold,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/acceptance/low-cost-real.json")
    )
    parser.add_argument(
        "--budget-ledger", type=Path, default=Path("artifacts/acceptance/budget.sqlite3")
    )
    args = parser.parse_args()
    if not args.approved:
        print("status=LOW_COST_REAL_ACCEPTANCE_APPROVAL_REQUIRED")
        return 2
    loaded = load_env(ROOT)
    report = build_env_report(loaded, "G4")
    if loaded.settings is None or not report["summary"]["gate_ready"]:  # type: ignore[index]
        print(json.dumps({"status": "G4_CONFIG_NOT_READY", "blockers": report["gate_blockers"]}))
        return 3
    if (
        loaded.settings.app_env != "production"
        or loaded.settings.rag_runtime_profile != "production"
        or not loaded.settings.real_provider_calls_enabled
    ):
        print("status=PRODUCTION_REAL_PROVIDER_PROFILE_REQUIRED")
        return 3
    if (
        loaded.settings.real_acceptance_max_provider_calls != 60
        or loaded.settings.real_acceptance_max_input_tokens != 200_000
        or loaded.settings.real_acceptance_max_output_tokens != 20_000
    ):
        print("status=LOW_COST_ACCEPTANCE_BUDGET_MISMATCH")
        return 3
    key = os.environ.get("RAG_GOLD_SIGNING_KEY", "").encode()
    gold_path = args.gold if args.gold.is_absolute() else ROOT / args.gold
    output = args.output if args.output.is_absolute() else ROOT / args.output
    budget = args.budget_ledger if args.budget_ledger.is_absolute() else ROOT / args.budget_ledger
    try:
        runner = LowCostRealAcceptanceRunner(
            ROOT,
            loaded.settings,
            load_gold(gold_path),
            key,
            budget,
            output.parent,
        )
    except ValueError as error:
        print(json.dumps({"status": str(error), "provider_calls": 0}, sort_keys=True))
        return 4
    result = runner.run()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "PASSED" if result["passed"] else "FAILED",
                "output": str(output),
                "budget": result["budget"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if result["passed"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
