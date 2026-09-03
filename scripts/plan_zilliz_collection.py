"""Render the exact Zilliz G2 collection plan without executing cloud mutations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.config import build_env_report, load_env  # noqa: E402
from ragkb.infrastructure.zilliz_plan import build_zilliz_collection_plan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a non-executing Zilliz create plan")
    parser.add_argument("--output", type=Path, default=Path("artifacts/g2/zilliz-create-plan.json"))
    args = parser.parse_args()
    loaded = load_env(ROOT)
    report = build_env_report(loaded, "G2")
    if loaded.settings is None or not report["summary"]["gate_ready"]:  # type: ignore[index]
        print(
            json.dumps(
                {
                    "status": "G2_CONFIG_NOT_READY",
                    "gate_blockers": report["gate_blockers"],
                    "mutating_call_performed": False,
                },
                sort_keys=True,
            )
        )
        return 2
    plan = build_zilliz_collection_plan(loaded.settings)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "ZILLIZ_COLLECTION_CREATE_APPROVAL_REQUIRED",
                "plan_revision": plan["plan_revision"],
                "schema_fingerprint": plan["schema_fingerprint"],
                "field_count": len(plan["schema"]["fields"]),
                "index_count": len(plan["schema"]["indexes"]),
                "mutating_call_performed": False,
                "secret_values_in_output": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
