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
    real_format_path = ROOT / "artifacts/final-validation/real-format-validation.json"
    real_format_complete = False
    if real_format_path.is_file():
        loaded = json.loads(real_format_path.read_text(encoding="utf-8"))
        real_format_complete = bool(
            isinstance(loaded, dict)
            and loaded.get("real_acceptance") is True
            and loaded.get("format_quality_ready") is True
            and loaded.get("totals", {}).get("sample_count") == 50
        )
    blockers = [
        blocker
        for blocker in FINAL_REAL_EVIDENCE_REQUIREMENTS
        if not (blocker == "REAL_FORMAT_SAMPLES_NON_ASR_5_X_10_REQUIRED" and real_format_complete)
    ]
    return {
        "revision": "final-unified-validation-plan:v1",
        "status": "BLOCKED_REAL_EVIDENCE_MISSING",
        "suites": [
            "non_asr_real_formats_5x10",
            "real_model_quality_cost_and_safety",
            "mysql_g3_g4_migration",
            "zilliz_redis_mysql_lifecycle_drill",
            "production_like_performance_long_run_restore",
            "real_uat",
        ],
        "blockers": blockers,
        "completed_suites": ["non_asr_real_formats_5x10"] if real_format_complete else [],
        "real_format_acceptance": real_format_complete,
        "synthetic_evidence_can_unlock": False,
        "real_acceptance": False,
        "external_call_performed": False,
        "scope": {
            "original_full_g6_scope_includes_7_day_observation": True,
            "real_7_day_observation": "deferred_by_user",
        },
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
