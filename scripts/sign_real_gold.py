"""Apply a business-review signature to an already completed ten-case Gold Dataset."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.evaluation.real_gold import sign_gold_dataset, validate_real_gold_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--reviewed-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.approved:
        print("status=BUSINESS_GOLD_APPROVAL_REQUIRED")
        return 2
    key = os.environ.get("RAG_GOLD_SIGNING_KEY", "").encode()
    source = args.dataset if args.dataset.is_absolute() else ROOT / args.dataset
    output = args.output if args.output.is_absolute() else ROOT / args.output
    loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise SystemExit("REAL_GOLD_DATASET_INVALID")
    loaded.update(
        status="APPROVED",
        reviewer_id=args.reviewer_id,
        reviewed_at=args.reviewed_at,
    )
    loaded["signature"] = sign_gold_dataset(loaded, key)
    report = validate_real_gold_dataset(loaded, key, required_cases=10)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(loaded, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
