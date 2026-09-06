"""Initialize or inspect the approved current local SQLite schema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.infrastructure.sqlite import SCHEMA_REVISION  # noqa: E402
from ragkb.runtime_components import build_runtime_components  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize the current local SQLite schema")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    components = build_runtime_components()
    components.database.initialize()
    with components.database.connect() as connection:
        value = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_revision'"
        ).fetchone()
    mode = "checked" if args.check else "initialized"
    print(
        f"migration={mode} adapter=sqlite_local schema_revision={value['value']} "
        f"expected={SCHEMA_REVISION}"
    )
    return 0 if value["value"] == SCHEMA_REVISION else 2


if __name__ == "__main__":
    raise SystemExit(main())
