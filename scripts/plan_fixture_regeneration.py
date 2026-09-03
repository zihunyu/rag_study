"""Content-free dry-run planner for dynamically flagged fixture regeneration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def plan(scan: dict) -> dict:
    records = []
    blocked = []
    for item in scan["records"]:
        if item.get("representation_status") == "DEFERRED_BY_USER":
            continue
        if item.get("glyph_render_defect") is True:
            records.append(
                {
                    "fixture_ref": item.get("fixture_ref"),
                    "source_sha256": item.get("source_sha256"),
                    "renderer_policy": "generic-local-renderer-v1",
                    "font_policy": "glyph-capability-fallback-v1",
                    "metadata_lineage_action": "UPDATE_AFTER_ATOMIC_REBUILD",
                }
            )
        elif item.get("representation_status") != "AVAILABLE":
            blocked.append(
                {
                    "fixture_ref": item.get("fixture_ref"),
                    "source_sha256": item.get("source_sha256"),
                    "status": "BLOCKED_NO_INDEPENDENT_REPRESENTATION",
                }
            )
    return {
        "revision": "fixture-regeneration-plan:v1",
        "dry_run": True,
        "rebuild_count": len(records),
        "blocked_count": len(blocked),
        "records": records,
        "blocked_records": blocked,
        "provider_call_count": 0,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scan", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    r = plan(json.loads(a.scan.read_text(encoding="utf-8")))
    a.output.write_text(json.dumps(r, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
