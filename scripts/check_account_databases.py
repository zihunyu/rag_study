"""Run the same account invariants in disposable SQLite and real MySQL databases."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.auth import AuthenticationError  # noqa: E402
from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter  # noqa: E402
from ragkb.config import load_env  # noqa: E402
from ragkb.infrastructure.account_lock import permission_guard  # noqa: E402
from ragkb.infrastructure.accounts import AccountRuleError, AccountService  # noqa: E402
from ragkb.infrastructure.mysql_migrations import apply_mysql_migrations  # noqa: E402
from ragkb.infrastructure.sqlite import SQLiteDatabase  # noqa: E402
from ragkb.infrastructure.workspace_db import WorkspaceDB  # noqa: E402


class Repository:
    def get_space(self, identity):
        return {"id": identity, "name": identity, "tenant_id": "account-fixture"}

    def list_spaces(self):
        return [self.get_space(s) for s in ["A", "B", "C"]]


def check(db, settings):
    service = AccountService(db, "account-fixture", settings, Repository())
    service.bootstrap("admin", "Isolated-only admin password!")
    token, _ = service.new_session(settings.auth_local_user_id)
    admin = service.authenticate(token)
    data = service.create_user(admin, "mixed", "Mixed", "member")
    user = data["user"]["id"]
    service.change_password(
        service.principal(user), data["temporary_password"], "Isolated-only user password!"
    )
    user_token, _ = service.new_session(user)
    service.set_members(admin, "A", [{"user_id": user, "role": "manager"}], 1)
    service.set_members(admin, "B", [{"user_id": user, "role": "qa"}], 1)
    subject = service.authenticate(user_token)
    assert [(s["id"], s["my_role"]) for s in service.allowed_spaces(subject)] == [
        ("A", "manager"),
        ("B", "qa"),
    ]
    assert service.recheck(user, subject.scope_tokens, ("A", "B"))
    service.set_members(admin, "A", [{"user_id": user, "role": "none"}], 2)
    assert not service.recheck(user, subject.scope_tokens, ("A",))
    try:
        service.update_user(admin, admin.user_id, {"enabled": False}, 1)
    except AccountRuleError as error:
        assert str(error) == "LAST_SUPER_ADMIN_REQUIRED"
    else:
        raise AssertionError("Last administrator lost")
    reset = service.reset_password(admin, user, service.user(user)["row_version"])
    assert reset and service.user(user)["must_change_password"]
    try:
        service.authenticate(user_token)
    except AuthenticationError:
        pass
    else:
        raise AssertionError("Old session not revoked")
    deleted = service.set_deleted(admin, "B", True, 2, "del")
    assert service.set_deleted(admin, "B", True, 2, "del") == deleted
    service.set_deleted(admin, "B", False, deleted["row_version"], "restore")
    with db.connection() as c:
        assert db.one(c, "SELECT COUNT(*) n FROM account_audit_events")["n"] >= 8
    return {
        "roles": True,
        "revocation": True,
        "last_super": True,
        "reset": True,
        "soft_delete_restore": True,
        "idempotency": True,
        "audit": True,
    }


def main():
    settings = load_env().settings
    assert settings is not None
    settings = settings.model_copy(update={"auth_mode": "password"})
    if len(sys.argv) == 3 and sys.argv[1] == "--lock-probe":
        target = sys.argv[2]
        assert re.fullmatch(r"ragkb_auth_acceptance_[a-f0-9]{16}", target)
        adapter = MySQLControlPlaneAdapter(settings.model_copy(update={"mysql_database": target}))
        try:
            with adapter.connect() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    "SELECT GET_LOCK(%s,0)",
                    ("ragkb-auth-" + hashlib.sha256(b"account-fixture").hexdigest()[:40],),
                )
                print(int(cursor.fetchone()[0]))
        finally:
            adapter.close()
        return
    database_name = "ragkb_auth_acceptance_" + uuid.uuid4().hex[:16]
    assert re.fullmatch(r"ragkb_auth_acceptance_[a-f0-9]{16}", database_name)
    report = {}
    with tempfile.TemporaryDirectory(prefix="account-databases-") as folder:
        local = SQLiteDatabase(Path(folder) / "local.sqlite3")
        local.initialize()
        report["sqlite"] = check(WorkspaceDB(local), settings)
        creator = MySQLControlPlaneAdapter(settings)
        connection = creator._connect(include_database=False)
        try:
            connection.cursor().execute(f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4")
            connection.commit()
            scoped = settings.model_copy(update={"mysql_database": database_name})
            mysql = MySQLControlPlaneAdapter(scoped)
            try:
                with mysql.connect() as migration:
                    report["migration"] = apply_mysql_migrations(migration)
                report["mysql"] = check(WorkspaceDB(local, mysql), scoped)
                with permission_guard(WorkspaceDB(local, mysql), "account-fixture"):
                    probe = subprocess.run(  # noqa: S603 - own script and unique fixture database
                        [sys.executable, __file__, "--lock-probe", database_name],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=20,
                    )
                    assert probe.stdout.strip() == "0"
                    report["mysql_cross_process_lock"] = True
                with mysql.connect() as migration:
                    repeated = apply_mysql_migrations(migration)
                    assert repeated["applied_count"] == 0
                    report["repeated_migration_is_noop"] = True
            finally:
                mysql.close()
        finally:
            # Only the exact unique fixture database created above may be removed.
            assert re.fullmatch(r"ragkb_auth_acceptance_[a-f0-9]{16}", database_name)
            connection.cursor().execute(f"DROP DATABASE IF EXISTS `{database_name}`")
            connection.commit()
            connection.close()
            creator.close()
    target = ROOT / "artifacts/reviews/20260908-account-access/database-consistency.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"sqlite": "passed", "mysql": "passed", "fixture_database_removed": True}))


if __name__ == "__main__":
    main()
