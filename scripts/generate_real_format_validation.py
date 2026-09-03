"""Generate anonymous unified evidence for the completed five-format real validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.evaluation.real_format_validation import (  # noqa: E402
    build_real_format_validation,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_real_format_validation(ROOT)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "revision": report["revision"],
                "totals": report["totals"],
                "embedding_coverage": report["embedding_coverage"],
                "uat": report["uat"],
                "format_quality_ready": report["format_quality_ready"],
                "real_acceptance": report["real_acceptance"],
                "external_call_count_this_stage": report["external_call_count_this_stage"],
                "content_output": False,
                "source_names_output": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
