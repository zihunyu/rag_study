"""Account and per-library authorization; all policy mutations are durable and audited."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from ragkb.adapters.auth import AuthenticationError, AuthorizationError
from ragkb.config import EnvSettings
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.ids import new_uuid7
from ragkb.domain.uploads import OptimisticConcurrencyError, ResourceNotFoundError
from ragkb.infrastructure.account_lock import permission_guard
from ragkb.infrastructure.workspace_db import WorkspaceDB

HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(24))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AccountRuleError(ValueError):
    pass


def password_valid(password: str) -> None:
    if not 15 <= len(password) <= 128:
        raise AccountRuleError("PASSWORD_LENGTH_15_128")


class AccountService:
    def __init__(self, db: WorkspaceDB, tenant_id: str, settings: EnvSettings, repository: Any):
        self.db, self.tenant_id, self.settings, self.repository = (
            db,
            tenant_id,
            settings,
            repository,
        )

    @property
    def enabled(self) -> bool:
        return self.settings.auth_mode == "password"

    @contextmanager
    def guard(self) -> Iterator[None]:
        with permission_guard(self.db, self.tenant_id):
            yield

    def user(self, user_id: str, connection: Any = None) -> dict[str, Any]:
        if connection is None:
            with self.db.connection() as own:
                return self.user(user_id, own)
        row = self.db.one(
            connection,
            "SELECT * FROM auth_users WHERE tenant_id=? AND id=?",
            (self.tenant_id, user_id),
        )
        if not row:
            raise ResourceNotFoundError(user_id)
        return row

    @staticmethod
    def public_user(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: row[key]
            for key in (
                "id",
                "username",
                "display_name",
                "global_role",
                "enabled",
                "must_change_password",
                "row_version",
                "created_at",
                "updated_at",
            )
        }

    def audit(
        self,
        connection: Any,
        actor: str,
        action: str,
        target: str,
        space: str = "",
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        self.db.execute(
            connection,
            "INSERT INTO account_audit_events "
            "(id,tenant_id,actor_id,action,target_id,space_id,detail_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                new_uuid7(),
                self.tenant_id,
                actor,
                action,
                target,
                space,
                json.dumps(detail or {}, ensure_ascii=False),
                time.time(),
            ),
        )

    def bootstrap(self, username: str, password: str) -> dict[str, Any]:
        password_valid(password)
        with self.guard(), self.db.transaction() as connection:
            if self.db.one(
                connection, "SELECT id FROM auth_users WHERE tenant_id=? LIMIT 1", (self.tenant_id,)
            ):
                raise AccountRuleError("ACCOUNTS_ALREADY_INITIALIZED")
            return self._create(
                connection,
                self.settings.auth_local_user_id,
                username,
                "总管理员",
                "super_admin",
                password,
                False,
                "bootstrap",
            )

    def _create(
        self,
        connection: Any,
        identity: str,
        username: str,
        display: str,
        role: str,
        password: str,
        temporary: bool,
        actor: str,
    ) -> dict[str, Any]:
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.@-]{2,63}", username):
            raise AccountRuleError("USERNAME_INVALID")
        if role not in {"super_admin", "member"} or not display.strip():
            raise AccountRuleError("USER_FIELDS_INVALID")
        if self.db.one(
            connection,
            "SELECT id FROM auth_users WHERE tenant_id=? AND username=?",
            (self.tenant_id, username),
        ):
            raise AccountRuleError("USERNAME_ALREADY_EXISTS")
        now = time.time()
        self.db.execute(
            connection,
            "INSERT INTO auth_users "
            "(id,tenant_id,username,display_name,password_hash,global_role,"
            "must_change_password,password_expires_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                identity,
                self.tenant_id,
                username,
                display.strip(),
                HASHER.hash(password),
                role,
                int(temporary),
                now + self.settings.auth_temporary_password_seconds if temporary else 0,
                now,
                now,
            ),
        )
        self.audit(connection, actor, "user.create", identity)
        return self.public_user(self.user(identity, connection))

    def require_super(self, subject: RequestPrincipal, connection: Any = None) -> None:
        row = self.user(subject.user_id, connection)
        if (
            subject.tenant_id != self.tenant_id
            or not row["enabled"]
            or row["must_change_password"]
            or row["global_role"] != "super_admin"
        ):
            raise AuthorizationError("SUPER_ADMIN_REQUIRED")

    def create_user(
        self, subject: RequestPrincipal, username: str, display: str, role: str
    ) -> dict[str, Any]:
        temporary = secrets.token_urlsafe(24)
        with self.guard(), self.db.transaction() as connection:
            self.require_super(subject, connection)
            row = self._create(
                connection, new_uuid7(), username, display, role, temporary, True, subject.user_id
            )
        return {
            "user": row,
            "temporary_password": temporary,
            "expires_in": self.settings.auth_temporary_password_seconds,
        }

    @staticmethod
    def match(row: Mapping[str, Any], expected: int) -> None:
        if row["row_version"] != expected:
            raise OptimisticConcurrencyError("AUTHORIZATION_CHANGED_REFRESH_REQUIRED")

    def update_user(
        self, subject: RequestPrincipal, user_id: str, patch: Mapping[str, Any], expected: int
    ) -> dict[str, Any]:
        with self.guard(), self.db.transaction() as connection:
            self.require_super(subject, connection)
            row = self.user(user_id, connection)
            self.match(row, expected)
            role = patch.get("global_role", row["global_role"])
            enabled = bool(patch.get("enabled", row["enabled"]))
            display = str(patch.get("display_name", row["display_name"])).strip()
            if role not in {"super_admin", "member"} or not display:
                raise AccountRuleError("USER_FIELDS_INVALID")
            if (
                row["global_role"] == "super_admin"
                and row["enabled"]
                and (not enabled or role != "super_admin")
            ):
                count = self.db.one(
                    connection,
                    "SELECT COUNT(*) AS n FROM auth_users "
                    "WHERE tenant_id=? AND enabled=1 AND global_role='super_admin' "
                    "AND must_change_password=0",
                    (self.tenant_id,),
                )
                if count and count["n"] <= 1:
                    raise AccountRuleError("LAST_SUPER_ADMIN_REQUIRED")
            self.db.execute(
                connection,
                "UPDATE auth_users SET display_name=?,global_role=?,"
                "enabled=?,auth_revision=auth_revision+1,row_version=row_version+1,"
                "updated_at=? WHERE id=? AND tenant_id=?",
                (display, role, int(enabled), time.time(), user_id, self.tenant_id),
            )
            if not enabled or role != row["global_role"]:
                self.revoke_sessions(connection, user_id)
            self.audit(
                connection,
                subject.user_id,
                "user.update",
                user_id,
                detail={"enabled": enabled, "global_role": role},
            )
            return self.public_user(self.user(user_id, connection))

    def revoke_sessions(self, connection: Any, user_id: str) -> None:
        self.db.execute(
            connection,
            "UPDATE auth_sessions SET revoked=1 WHERE tenant_id=? AND user_id=?",
            (self.tenant_id, user_id),
        )

    def reset_password(self, subject: RequestPrincipal, user_id: str, expected: int) -> str:
        if user_id == subject.user_id:
            raise AccountRuleError("RESET_SELF_USE_PASSWORD_CHANGE")
        temporary = secrets.token_urlsafe(24)
        with self.guard(), self.db.transaction() as connection:
            self.require_super(subject, connection)
            self.match(self.user(user_id, connection), expected)
            self._password(connection, user_id, temporary, True)
            self.audit(connection, subject.user_id, "password.reset", user_id)
        return temporary

    def _password(self, connection: Any, user_id: str, password: str, temporary: bool) -> None:
        password_valid(password)
        self.db.execute(
            connection,
            "UPDATE auth_users SET password_hash=?,must_change_password=?,"
            "password_expires_at=?,auth_revision=auth_revision+1,row_version=row_version+1,"
            "updated_at=? WHERE tenant_id=? AND id=?",
            (
                HASHER.hash(password),
                int(temporary),
                time.time() + self.settings.auth_temporary_password_seconds if temporary else 0,
                time.time(),
                self.tenant_id,
                user_id,
            ),
        )
        self.revoke_sessions(connection, user_id)

    @staticmethod
    def verify(password_hash: str, password: str) -> bool:
        try:
            return bool(HASHER.verify(password_hash, password))
        except (VerificationError, InvalidHashError):
            return False

    def change_password(self, subject: RequestPrincipal, current: str, password: str) -> None:
        password_valid(password)
        with self.guard(), self.db.transaction() as connection:
            row = self.user(subject.user_id, connection)
            if not row["enabled"] or not self.verify(row["password_hash"], current):
                raise AuthenticationError("CURRENT_PASSWORD_INVALID")
            if current == password:
                raise AccountRuleError("NEW_PASSWORD_MUST_DIFFER")
            self._password(connection, subject.user_id, password, False)
            self.audit(connection, subject.user_id, "password.change", subject.user_id)

    def credentials(self, username: str, password: str) -> str:
        with self.db.connection() as connection:
            row = self.db.one(
                connection,
                "SELECT * FROM auth_users WHERE tenant_id=? AND username=?",
                (self.tenant_id, username.strip().lower()),
            )
        valid = self.verify(str(row["password_hash"]) if row else DUMMY_HASH, password[:129])
        if (
            not valid
            or not row
            or not row["enabled"]
            or (row["password_expires_at"] and row["password_expires_at"] <= time.time())
        ):
            raise AuthenticationError("LOGIN_FAILED")
        return str(row["id"])

    def new_session(self, user_id: str | None = None) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        csrf = digest("csrf:" + token)
        now = time.time()
        with self.db.transaction() as connection:
            self.db.execute(
                connection,
                "INSERT INTO auth_sessions "
                "(token_hash,tenant_id,user_id,csrf_hash,created_at,last_seen_at,expires_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    digest(token),
                    self.tenant_id,
                    user_id,
                    digest(csrf),
                    now,
                    now,
                    now + (self.settings.auth_session_absolute_seconds if user_id else 600),
                ),
            )
        return token, csrf

    def session(self, token: str, *, hashed: bool = False, touch: bool = False) -> dict[str, Any]:
        now = time.time()
        with self.db.transaction() as connection:
            row = self.db.one(
                connection,
                "SELECT * FROM auth_sessions WHERE token_hash=? AND tenant_id=?",
                (token if hashed else digest(token), self.tenant_id),
            )
            if (
                not row
                or row["revoked"]
                or row["expires_at"] <= now
                or row["last_seen_at"] + self.settings.auth_session_idle_seconds <= now
            ):
                raise AuthenticationError("SESSION_EXPIRED")
            if touch and row["last_seen_at"] < now - 30:
                self.db.execute(
                    connection,
                    "UPDATE auth_sessions SET last_seen_at=? WHERE token_hash=?",
                    (now, row["token_hash"]),
                )
            return row

    def logout(self, token: str) -> None:
        with self.guard(), self.db.transaction() as connection:
            self.db.execute(
                connection,
                "UPDATE auth_sessions SET revoked=1 WHERE token_hash=? AND tenant_id=?",
                (digest(token), self.tenant_id),
            )

    def principal(self, user_id: str, session_hash: str = "") -> RequestPrincipal:
        row = self.user(user_id)
        if not row["enabled"] or (
            row["must_change_password"] and row["password_expires_at"] <= time.time()
        ):
            raise AuthenticationError("ACCOUNT_UNAVAILABLE")
        memberships = self.memberships(user_id)
        admin = row["global_role"] == "super_admin"
        roles = (
            ("admin", "knowledge_maintainer", "reader")
            if admin
            else (
                ("knowledge_maintainer", "reader")
                if any(m["role"] == "manager" for m in memberships)
                else ("reader",)
            )
        )
        tokens = [
            f"tenant:{self.tenant_id}",
            f"user:{user_id}",
            f"auth-revision:{row['auth_revision']}",
            *(f"role:{r}" for r in roles),
        ]
        for member in memberships:
            tokens.append(f"space:{member['space_id']}:ask")
            if member["role"] == "manager":
                tokens.append(f"space:{member['space_id']}:manage")
        if session_hash:
            tokens.append("auth-session:" + session_hash)
        return RequestPrincipal(self.tenant_id, user_id, roles, tuple(tokens), "password", 3)

    def authenticate(self, token: str, *, touch: bool = True) -> RequestPrincipal:
        session = self.session(token, touch=touch)
        if not session["user_id"]:
            raise AuthenticationError("LOGIN_REQUIRED")
        return self.principal(str(session["user_id"]), str(session["token_hash"]))

    def memberships(self, user_id: str) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            return self.db.rows(
                connection,
                "SELECT * FROM space_memberships WHERE tenant_id=? AND user_id=?",
                (self.tenant_id, user_id),
            )

    def space_state(self, space_id: str, connection: Any = None) -> dict[str, Any]:
        space = self.repository.get_space(space_id)
        if space["tenant_id"] != self.tenant_id:
            raise ResourceNotFoundError(space_id)
        if connection is None:
            with self.db.connection() as own:
                return self.space_state(space_id, own)
        return self.db.one(
            connection,
            "SELECT * FROM space_access_state WHERE tenant_id=? AND space_id=?",
            (self.tenant_id, space_id),
        ) or {"space_id": space_id, "deleted": 0, "row_version": 1}

    def require_space(
        self,
        subject: RequestPrincipal,
        space_id: str,
        *,
        manage: bool = False,
        include_deleted: bool = False,
    ) -> str:
        if subject.tenant_id != self.tenant_id:
            raise ResourceNotFoundError(space_id)
        user = self.user(subject.user_id)
        state = self.space_state(space_id)
        if (
            not user["enabled"]
            or user["must_change_password"]
            or (state["deleted"] and not include_deleted)
        ):
            raise ResourceNotFoundError(space_id)
        if user["global_role"] == "super_admin":
            return "manager"
        membership = next(
            (m for m in self.memberships(subject.user_id) if m["space_id"] == space_id), None
        )
        if not membership or (manage and membership["role"] != "manager"):
            raise ResourceNotFoundError(space_id)
        return str(membership["role"])

    def allowed_spaces(
        self, subject: RequestPrincipal, *, manage: bool = False, include_deleted: bool = False
    ) -> list[dict[str, Any]]:
        result = []
        for space in self.repository.list_spaces():
            try:
                role = self.require_space(
                    subject, space["id"], manage=manage, include_deleted=include_deleted
                )
            except ResourceNotFoundError:
                continue
            state = self.space_state(space["id"])
            if state["deleted"] and role != "manager":
                continue
            result.append(
                {
                    **space,
                    "my_role": role,
                    **state,
                    "available_actions": (
                        ["restore"]
                        if state["deleted"]
                        else ["ask", "manage", "members", "delete"]
                        if role == "manager"
                        else ["ask"]
                    ),
                }
            )
        return result

    def _set_state(self, connection: Any, space: str, deleted: int, revision: int) -> None:
        self.db.execute(
            connection,
            "DELETE FROM space_access_state WHERE tenant_id=? AND space_id=?",
            (self.tenant_id, space),
        )
        self.db.execute(
            connection,
            "INSERT INTO space_access_state "
            "(tenant_id,space_id,deleted,row_version,updated_at) VALUES (?,?,?,?,?)",
            (self.tenant_id, space, deleted, revision, time.time()),
        )

    def set_members(
        self, subject: RequestPrincipal, space: str, changes: list[dict[str, str]], expected: int
    ) -> dict[str, Any]:
        with self.guard(), self.db.transaction() as connection:
            self.require_space(subject, space, manage=True)
            state = self.space_state(space, connection)
            self.match(state, expected)
            actor = self.user(subject.user_id, connection)
            if len({c["user_id"] for c in changes}) != len(changes):
                raise AccountRuleError("DUPLICATE_MEMBER")
            for change in changes:
                target = self.user(change["user_id"], connection)
                role = change["role"]
                old = self.db.one(
                    connection,
                    "SELECT role FROM space_memberships "
                    "WHERE tenant_id=? AND space_id=? AND user_id=?",
                    (self.tenant_id, space, target["id"]),
                )
                if role not in {"none", "qa", "manager"} or target["global_role"] == "super_admin":
                    raise AccountRuleError("MEMBERSHIP_ROLE_INVALID")
                if role != "none" and not target["enabled"]:
                    raise AccountRuleError("MEMBER_DISABLED")
                if actor["global_role"] != "super_admin" and (
                    role == "manager" or old and old["role"] == "manager"
                ):
                    raise AuthorizationError("MANAGER_ASSIGNMENT_REQUIRES_SUPER_ADMIN")
                self.db.execute(
                    connection,
                    "DELETE FROM space_memberships WHERE tenant_id=? AND space_id=? AND user_id=?",
                    (self.tenant_id, space, target["id"]),
                )
                if role != "none":
                    self.db.execute(
                        connection,
                        "INSERT INTO space_memberships "
                        "(tenant_id,space_id,user_id,role,assigned_by,created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (self.tenant_id, space, target["id"], role, subject.user_id, time.time()),
                    )
                self.db.execute(
                    connection,
                    "UPDATE auth_users SET auth_revision=auth_revision+1,"
                    "row_version=row_version+1 WHERE id=? AND tenant_id=?",
                    (target["id"], self.tenant_id),
                )
                self.audit(
                    connection,
                    subject.user_id,
                    "membership.change",
                    target["id"],
                    space,
                    {"before": old["role"] if old else "none", "after": role},
                )
            self._set_state(connection, space, 0, state["row_version"] + 1)
        return {"row_version": state["row_version"] + 1}

    def set_deleted(
        self, subject: RequestPrincipal, space: str, deleted: bool, expected: int, command_key: str
    ) -> dict[str, Any]:
        payload_hash = digest(json.dumps([space, deleted, expected]))
        with self.guard(), self.db.transaction() as connection:
            self.require_space(subject, space, manage=True, include_deleted=True)
            replay = self.db.one(
                connection,
                "SELECT * FROM account_commands WHERE tenant_id=? AND actor_id=? AND command_key=?",
                (self.tenant_id, subject.user_id, command_key),
            )
            if replay:
                if replay["payload_hash"] != payload_hash:
                    raise AccountRuleError("IDEMPOTENCY_PAYLOAD_CHANGED")
                return dict(json.loads(replay["result_json"]))
            state = self.space_state(space, connection)
            self.match(state, expected)
            self._set_state(connection, space, int(deleted), expected + 1)
            # Ensure revoke/regrant does not reuse a formerly captured QA identity.
            self.db.execute(
                connection,
                "UPDATE auth_users SET auth_revision=auth_revision+1 WHERE tenant_id=?",
                (self.tenant_id,),
            )
            self.audit(
                connection,
                subject.user_id,
                "space.delete" if deleted else "space.restore",
                space,
                space,
            )
            result = {"space_id": space, "deleted": deleted, "row_version": expected + 1}
            self.db.execute(
                connection,
                "INSERT INTO account_commands "
                "(tenant_id,actor_id,command_key,payload_hash,result_json) VALUES (?,?,?,?,?)",
                (self.tenant_id, subject.user_id, command_key, payload_hash, json.dumps(result)),
            )
            return result

    def recheck(self, user_id: str, scopes: tuple[str, ...], spaces: tuple[str, ...]) -> bool:
        try:
            current = self.principal(user_id)
            if not set(t for t in current.scope_tokens if t.startswith("auth-revision:")).issubset(
                scopes
            ):
                return False
            session_hash = next((t[13:] for t in scopes if t.startswith("auth-session:")), "")
            session = self.session(session_hash, hashed=True)
            if session["user_id"] != user_id:
                return False
            for space in spaces:
                self.require_space(current, space)
            return not self.user(user_id)["must_change_password"]
        except (AuthenticationError, ResourceNotFoundError):
            return False
