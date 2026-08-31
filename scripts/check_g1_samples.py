"""Enforce the five-format real-sample Gate without reading sample contents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.evaluation.g1_samples import check_g1_samples  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the G1 real sample Gate")
    parser.add_argument("--allow-blocked", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = check_g1_samples(ROOT, ROOT / "backend/tests/fixtures/manifests/format-samples.yaml")
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    return 0 if report["ready"] or args.allow_blocked else 2


if __name__ == "__main__":
    raise SystemExit(main())
