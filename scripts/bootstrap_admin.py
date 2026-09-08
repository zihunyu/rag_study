"""Interactive first-administrator initialization; never takes passwords in argv or env."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.infrastructure.accounts import AccountRuleError  # noqa: E402
from ragkb.infrastructure.mysql_migrations import (  # noqa: E402
    MYSQL_MIGRATIONS,
    apply_mysql_migrations,
)
from ragkb.runtime_components import build_runtime_components  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set the initial administrator password interactively"
    )
    parser.add_argument("--username", default="admin")
    parser.add_argument(
        "--activate", action="store_true", help="Enable password mode after initialization"
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--migrate-only",
        action="store_true",
        help="Apply additive account tables without creating users",
    )
    parser.add_argument(
        "--wait", action="store_true", help="Keep the interactive window open at exit"
    )
    args = parser.parse_args()
    runtime = build_runtime_components()
    accounts = runtime.accounts
    assert accounts is not None
    if accounts.db.mysql:
        with accounts.db.connection() as connection:
            recorded = {
                r["migration_id"]
                for r in accounts.db.rows(connection, "SELECT migration_id FROM schema_migrations")
            }
            pending = [identity for identity, _ in MYSQL_MIGRATIONS if identity not in recorded]
            permitted = {
                "create_" + name
                for name in (
                    "auth_users",
                    "auth_sessions",
                    "space_memberships",
                    "space_access_state",
                    "account_audit_events",
                    "account_commands",
                    "idx_auth_users_tenant",
                    "idx_auth_sessions_user",
                    "idx_space_members_user",
                    "idx_account_audit_scope",
                )
            }
            if pending and args.check:
                print(
                    json.dumps(
                        {
                            "initialized": False,
                            "mode": runtime.settings.auth_mode,
                            "migration_required": pending,
                        }
                    )
                )
                return 0
            if set(pending) - permitted:
                raise RuntimeError("UNRELATED_PENDING_MIGRATIONS_REQUIRE_NORMAL_MIGRATION_COMMAND")
            if pending:
                result = apply_mysql_migrations(connection)
                output = ROOT / "artifacts/reviews/20260908-account-access"
                output.mkdir(parents=True, exist_ok=True)
                (output / "current-environment-migration.json").write_text(
                    json.dumps(result, indent=2), encoding="utf-8"
                )
    if args.migrate_only:
        print("Account schema is ready. Existing knowledge and conversation tables were preserved.")
        return 0
    with accounts.db.connection() as connection:
        existing = accounts.db.one(
            connection,
            "SELECT id,username FROM auth_users "
            "WHERE tenant_id=? AND global_role='super_admin' AND enabled=1",
            (runtime.tenant_id,),
        )
    if args.check:
        print(json.dumps({"initialized": bool(existing), "mode": runtime.settings.auth_mode}))
        return 0
    if existing:
        print("Administrator already initialized. Existing credentials were not changed.")
        return 0
    print("RAGSPACE - Initial administrator setup")
    print("Account: " + args.username)
    print("Choose 15-128 characters. Input is hidden and is not written to project files.")
    while True:
        password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords did not match. Please try again.")
            continue
        try:
            accounts.bootstrap(args.username, password)
            break
        except AccountRuleError as error:
            print(str(error))
            if str(error) != "PASSWORD_LENGTH_15_128":
                return 2
        finally:
            password = confirm = ""
    if args.activate:
        env_path = ROOT / "config/.env"
        text = env_path.read_text(encoding="utf-8")
        updated, count = re.subn(r"(?m)^AUTH_MODE=.*$", "AUTH_MODE=password", text)
        if count != 1:
            raise RuntimeError("AUTH_MODE_ENTRY_NOT_UNIQUE; administrator was created")
        temporary = env_path.with_suffix(".env.auth-tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.replace(temporary, env_path)
        print("Password mode configured. Restart the backend and worker to load it.")
    print("Administrator initialized; prior documents and conversations were preserved.")
    if args.wait:
        input("Press Enter to close this window.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
