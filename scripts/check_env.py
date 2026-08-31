"""Validate config/.env without displaying any values."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.config import build_env_report, load_env  # noqa: E402


def _table(report: dict[str, object]) -> str:
    lines = [
        f"Requested Gate: {report['requested_gate']}",
        "VARIABLE | CONFIGURED | SOURCE | SECRET | TYPE",
    ]
    for item in report["variables"]:  # type: ignore[union-attr]
        lines.append("{name} | {configured} | {source} | {secret} | {type}".format(**item))
    lines.append("ISSUE_KEY | CODE | BLOCKING_GATE")
    for item in report["issues"]:  # type: ignore[union-attr]
        lines.append("{key} | {code} | {blocking_gate}".format(**item))
    lines.append("SUMMARY | " + json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    lines.append(str(report["safe_output_contract"]))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check typed config/.env readiness")
    parser.add_argument("--gate", choices=[f"G{i}" for i in range(7)], default="G0")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    parser.add_argument("--allow-blocked", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_env_report(load_env(ROOT), args.gate)
    rendered = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        if args.format == "json"
        else _table(report)
    )
    print(rendered)
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    ready = bool(report["summary"]["gate_ready"])  # type: ignore[index]
    return 0 if ready or args.allow_blocked else 2


if __name__ == "__main__":
    raise SystemExit(main())
