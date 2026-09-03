from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.evaluation.g4_validation import build_g4_local_validation_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the non-billable G4 local harness")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_g4_local_validation_report(ROOT)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    return 0 if report["local_preparation_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
