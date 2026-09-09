"""Inspect/apply only the additive QA acceptance tables in the configured MySQL database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.config import load_env
from ragkb.infrastructure.mysql_migrations import MYSQL_MIGRATIONS, apply_mysql_migrations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = load_env(Path(__file__).resolve().parents[1]).settings
    control = MySQLControlPlaneAdapter(settings)
    try:
        connection = control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT migration_id FROM schema_migrations")
            recorded = {r[0] for r in cursor.fetchall()}
            pending = [key for key, _ in MYSQL_MIGRATIONS if key not in recorded]
            if any(
                not key.startswith(("create_acceptance_", "create_idx_acceptance_"))
                for key in pending
            ):
                raise RuntimeError("UNRELATED_PENDING_MIGRATIONS_USE_NORMAL_UPGRADE")
            result = apply_mysql_migrations(connection) if args.apply else {"pending": pending}
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            connection.close()
    finally:
        control.close()


if __name__ == "__main__":
    main()
