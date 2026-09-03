"""Run the explicitly approved MySQL G2 database creation and migrations once."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.mysql_provision import (  # noqa: E402
    MYSQL_APPROVAL,
    MySQLProvisionError,
    provision_mysql_control_plane,
)
from ragkb.config import build_env_report, load_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision approved MySQL G2 control plane")
    parser.add_argument("--approval", required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/g2/mysql-provision-evidence.json")
    )
    args = parser.parse_args()
    if args.approval != MYSQL_APPROVAL:
        print("status=MYSQL_DATABASE_CREATE_AND_MIGRATE_APPROVAL_REQUIRED")
        return 2
    loaded = load_env(ROOT)
    gate = build_env_report(loaded, "G2")
    if loaded.settings is None or not gate["summary"]["gate_ready"]:  # type: ignore[index]
        print(json.dumps({"status": "G2_CONFIG_NOT_READY", "gate_blockers": gate["gate_blockers"]}))
        return 3
    try:
        evidence = provision_mysql_control_plane(loaded.settings, approval=args.approval)
    except MySQLProvisionError as error:
        evidence = {
            "status": "MYSQL_G2_PROVISION_FAILED",
            "stage": error.stage,
            "error_type": error.error_type,
            "mysql_error_code": error.mysql_error_code,
            "database_name_in_output": False,
            "host_in_output": False,
            "username_in_output": False,
            "password_in_output": False,
            "drop_statement_count": 0,
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
