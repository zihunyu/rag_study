"""Initialize or inspect the approved G1 local SQLite schema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.infrastructure.sqlite import SCHEMA_VERSION  # noqa: E402
from ragkb.runtime_components import build_runtime_components  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize the G1 local SQLite schema")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    components = build_runtime_components()
    components.database.initialize()
    with components.database.connect() as connection:
        value = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()
    mode = "checked" if args.check else "initialized"
    print(
        f"migration={mode} adapter=sqlite_local schema_version={value['value']} "
        f"expected={SCHEMA_VERSION}"
    )
    return 0 if int(value["value"]) == SCHEMA_VERSION else 2


if __name__ == "__main__":
    raise SystemExit(main())
