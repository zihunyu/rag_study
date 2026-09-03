"""Run explicitly approved Zilliz G2 creation and synthetic validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.zilliz_provision import (  # noqa: E402
    CREATE_APPROVAL,
    ZillizSyntheticLifecycleError,
    provision_and_validate,
)
from ragkb.config import build_env_report, load_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision approved Zilliz G2 resources")
    parser.add_argument("--approval", required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/g2/zilliz-provision-evidence.json")
    )
    args = parser.parse_args()
    if args.approval != CREATE_APPROVAL:
        print("status=ZILLIZ_COLLECTION_CREATE_APPROVAL_REQUIRED")
        return 2
    loaded = load_env(ROOT)
    gate = build_env_report(loaded, "G2")
    if loaded.settings is None or not gate["summary"]["gate_ready"]:  # type: ignore[index]
        print(
            json.dumps(
                {"status": "G2_CONFIG_NOT_READY", "gate_blockers": gate["gate_blockers"]},
                sort_keys=True,
            )
        )
        return 3
    try:
        evidence = provision_and_validate(loaded.settings, approval=args.approval)
    except ZillizSyntheticLifecycleError as error:
        evidence = {
            "status": "ZILLIZ_G2_PROVISION_OR_VALIDATION_FAILED",
            "stage": error.stage,
            "error_type": error.error_type,
            "error_code": error.error_code,
            "confirmed_count": error.confirmed_count,
            "cleaned_count": error.cleaned_count,
            "remaining_count": error.remaining_count,
            "endpoint_in_output": False,
            "token_in_output": False,
            "drop_operation_performed": False,
        }
        exit_code = 4
    except Exception as error:
        evidence = {
            "status": "ZILLIZ_G2_PROVISION_OR_VALIDATION_FAILED",
            "error_type": type(error).__name__,
            "endpoint_in_output": False,
            "token_in_output": False,
            "drop_operation_performed": False,
        }
        exit_code = 4
    else:
        exit_code = 0
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
