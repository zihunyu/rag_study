"""Run privacy-safe offline validation for authorized real samples."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.evaluation.local_sample_validation import (  # noqa: E402
    external_call_plan,
    render_safe_summary,
    validate_local_samples,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--details", type=Path, required=True)
    parser.add_argument("--external-plan", type=Path, required=True)
    args = parser.parse_args()
    report = validate_local_samples(
        ROOT, ROOT / "backend/tests/fixtures/manifests/format-samples.yaml"
    )
    details = args.details if args.details.is_absolute() else ROOT / args.details
    plan_path = (
        args.external_plan if args.external_plan.is_absolute() else ROOT / args.external_plan
    )
    details.parent.mkdir(parents=True, exist_ok=True)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    details.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan_path.write_text(
        json.dumps(external_call_plan(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(render_safe_summary(report))
    failures = sum(
        item["status"] == "FAILED"
        for item in report["samples"]  # type: ignore[union-attr]
    )
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
