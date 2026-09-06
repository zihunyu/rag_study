"""Generate the deferred real-evidence unified validation plan and blocked report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.domain.governance import FINAL_REAL_EVIDENCE_REQUIREMENTS  # noqa: E402


def build_plan() -> dict[str, object]:
    blockers = list(FINAL_REAL_EVIDENCE_REQUIREMENTS)
    return {
        "revision": "final-unified-validation-plan",
        "status": "BLOCKED_REAL_EVIDENCE_MISSING",
        "suites": [
            "non_asr_real_formats_5x10",
            "real_model_quality_cost_and_safety",
            "database_initialization",
            "zilliz_redis_mysql_lifecycle_drill",
            "production_like_performance_long_run_restore",
            "real_uat",
        ],
        "blockers": blockers,
        "completed_suites": [],
        "real_format_acceptance": False,
        "synthetic_evidence_can_unlock": False,
        "real_acceptance": False,
        "external_call_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_plan()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
