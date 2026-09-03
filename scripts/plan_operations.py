"""Generate plan-only migration/reconciliation/backup/restore/rollback orchestration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_plan() -> dict[str, object]:
    return {
        "revision": "operations-plan:g5-v1",
        "steps": [
            "preflight_config_and_secret_status",
            "sqlite_schema_and_file_lineage_snapshot",
            "mysql_and_zilliz_reconciliation_read_only",
            "backup_manifest_and_checksums",
            "tombstone_first_restore",
            "publication_pointer_and_candidate_reconcile",
            "rollback_readiness_check",
        ],
        "external_mutation_performed": False,
        "approval_required": [
            "MYSQL_G3_G4_MIGRATION_APPROVAL_REQUIRED",
            "EXTERNAL_LIFECYCLE_DRILL_APPROVAL_REQUIRED",
        ],
        "simulated": True,
        "real_acceptance": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    plan = build_plan()
    rendered = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
