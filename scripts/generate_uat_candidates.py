"""Generate local content-free UAT candidates and stop for user review."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.evaluation.uat_candidates import generate_uat_candidates  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = generate_uat_candidates(
        ROOT, ROOT / "backend/tests/fixtures/manifests/format-samples.yaml"
    )
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    categories = Counter(
        str(item["source_category"])
        for item in report["candidates"]  # type: ignore[union-attr]
    )
    print(
        json.dumps(
            {
                "candidate_count": report["candidate_count"],
                "category_counts": dict(sorted(categories.items())),
                "status": "PENDING_USER_REVIEW",
                "model_call_count": 0,
                "network_call_count": 0,
                "question_text_output": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
