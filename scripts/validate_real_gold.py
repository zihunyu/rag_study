"""Validate, but never fabricate or approve, the ten-case business Gold Dataset."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.evaluation.real_gold import validate_real_gold_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    args = parser.parse_args()
    key = os.environ.get("RAG_GOLD_SIGNING_KEY", "").encode()
    path = args.dataset if args.dataset.is_absolute() else ROOT / args.dataset
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise SystemExit("REAL_GOLD_DATASET_INVALID")
    report = validate_real_gold_dataset(loaded, key, required_cases=10)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
