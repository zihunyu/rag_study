"""Run a configurable, non-billable end-to-end local performance baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.evaluation.system_performance import run_representative_system_paths  # noqa: E402


def _numbers(value: str) -> tuple[int, ...]:
    parsed = tuple(int(item) for item in value.split(",") if item.strip())
    if not parsed or any(item < 1 for item in parsed):
        raise argparse.ArgumentTypeError("values must be positive comma-separated integers")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=_numbers, default=(10, 100))
    parser.add_argument("--concurrency", type=_numbers, default=(1, 10, 50))
    parser.add_argument("--output", type=Path, default=Path("artifacts/performance/local.json"))
    args = parser.parse_args()
    report = run_representative_system_paths(
        ROOT,
        document_scales=args.documents,
        concurrency_values=args.concurrency,
    )
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "failure_count": report["failure_count"]}))
    return int(int(report["failure_count"]) > 0)


if __name__ == "__main__":
    raise SystemExit(main())
