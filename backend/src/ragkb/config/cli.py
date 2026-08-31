"""Command-line interface for safe configuration validation."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ragkb.config.loader import find_repository_root, load_configuration
from ragkb.config.validation import build_validation_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate RAG project inputs without exposing secrets"
    )
    parser.add_argument("--gate", default="G0", choices=[f"G{i}" for i in range(7)])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--format", choices=["table", "json"], default="table")
    parser.add_argument("--write-report", type=Path)
    parser.add_argument(
        "--allow-blocked",
        action="store_true",
        help="return success for a well-formed report even when the requested Gate is blocked",
    )
    return parser


def _render_table(report: Mapping[str, Any]) -> str:
    lines = [
        f"Requested Gate: {report['requested_gate']}",
        "PATH | PRIORITY | BLOCKING_GATE | RESPONSIBILITY | EFFECTIVE_SOURCE | "
        "BLOCKS_REQUESTED_GATE",
    ]
    for item in report["missing_inputs"]:
        lines.append(
            "{path} | {priority} | {blocking_gate} | {responsibility} | {effective_source} | "
            "{blocks_requested_gate}".format(**item)
        )
    lines.append(
        "SECRET_ENV_NAME | CONFIGURED | SOURCE | PRIORITY | BLOCKING_GATE | "
        "REQUIRED_FOR_CURRENT_MODE"
    )
    for item in report["secret_environment"]:
        lines.append(
            "{name} | {configured} | {source} | {priority} | {blocking_gate} | "
            "{required_for_current_mode}".format(**item)
        )
    summary = report["summary"]
    lines.append(f"SUMMARY | {json.dumps(summary, ensure_ascii=False, sort_keys=True)}")
    lines.append(str(report["attestation"]))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = find_repository_root()
    loaded = load_configuration(root, args.config, args.env_file)
    report = build_validation_report(loaded, args.gate)
    rendered = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        if args.format == "json"
        else _render_table(report)
    )
    print(rendered)
    if args.write_report:
        output_path = args.write_report
        if not output_path.is_absolute():
            output_path = root / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if report["schema_errors"]:
        return 3
    if not report["summary"]["gate_ready"] and not args.allow_blocked:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
