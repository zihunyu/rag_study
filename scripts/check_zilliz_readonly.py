"""Read-only Zilliz database, collection and schema inspection; never mutates cloud state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.zilliz import ZillizCloudAdapter  # noqa: E402
from ragkb.config import build_env_report, load_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Zilliz Cloud without mutations")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    loaded = load_env(ROOT)
    gate = build_env_report(loaded, "G2")
    if loaded.settings is None or not gate["summary"]["gate_ready"]:  # type: ignore[index]
        report: dict[str, object] = {
            "inspection_mode": "read_only",
            "error_code": "G2_CONFIG_NOT_READY",
            "gate_blockers": gate["gate_blockers"],
            "mutating_call_performed": False,
            "secret_values_in_output": False,
        }
        exit_code = 2
    else:
        adapter = ZillizCloudAdapter(loaded.settings)
        try:
            report = adapter.read_only_inspect()
            report["secret_values_in_output"] = False
            if report["zilliz_collection_create_approval_required"]:
                report["status"] = "ZILLIZ_COLLECTION_CREATE_APPROVAL_REQUIRED"
                exit_code = 3
            else:
                report["status"] = "READ_ONLY_SCHEMA_COMPATIBLE"
                exit_code = 0
        except Exception as error:
            report = {
                "inspection_mode": "read_only",
                "status": "ZILLIZ_READ_ONLY_INSPECTION_FAILED",
                "error_type": type(error).__name__,
                "mutating_call_performed": False,
                "secret_values_in_output": False,
            }
            exit_code = 4
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
