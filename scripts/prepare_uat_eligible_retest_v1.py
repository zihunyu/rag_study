"""Freeze an eligible-only future retest execution plan without executing it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("preflight", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    r = json.loads(a.preflight.read_text(encoding="utf-8"))
    eligible = [x for x in r["records"] if x["state"] == "ELIGIBLE"]
    plan = {
        "revision": "uat-eligible-retest-plan:v1",
        "eligible_count": len(eligible),
        "max_requests": len(eligible),
        "per_case_max_requests": 1,
        "automatic_retries": 0,
        "approved_by_user": False,
        "executed": False,
        "checkpoint_ref": "provider-checkpoints/uat-eligible-retest-v1.json",
        "result_ref": "uat-claim-results/eligible-retest-v1",
        "audit_ref": "uat-claim-audits/eligible-retest-v1",
        "coverage_ref": "uat-claim-audits/eligible-retest-v1/coverage.json",
        "provider_call_count": 0,
        "content_output": False,
    }
    a.output.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
