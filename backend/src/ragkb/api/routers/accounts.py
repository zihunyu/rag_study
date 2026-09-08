"""Password sessions, scoped membership administration, and explicit conversation audit."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from ragkb.adapters.auth import AuthenticationError
from ragkb.adapters.redis_cache import RedisCacheRateLimitAdapter
from ragkb.api.support import if_match, principal
from ragkb.domain.uploads import ResourceNotFoundError
from ragkb.infrastructure.accounts import AccountService, digest
from ragkb.runtime_components import RuntimeComponents

COOKIE = "ragkb_session"


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class PasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=15, max_length=128)


class CreateUser(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=100)
    global_role: Literal["member", "super_admin"] = "member"


class UpdateUser(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    global_role: Literal["member", "super_admin"] | None = None


class MemberChange(BaseModel):
    user_id: str = Field(min_length=1, max_length=191)
    role: Literal["none", "qa", "manager"]


class MemberChanges(BaseModel):
    changes: list[MemberChange] = Field(min_length=1, max_length=100)


class UserSpaceChange(BaseModel):
    space_id: str = Field(min_length=1, max_length=191)
    role: Literal["none", "qa", "manager"]


class AuditRead(BaseModel):
    reason: str = Field(min_length=3, max_length=500, pattern=r".*\S.*")


def build_accounts_router(runtime: RuntimeComponents, service: AccountService) -> APIRouter:
    router = APIRouter(tags=["accounts"])
    limiter = RedisCacheRateLimitAdapter(runtime.settings)

    def active() -> None:
        if not service.enabled:
            raise HTTPException(404, "PASSWORD_AUTH_NOT_ENABLED")

    def cookie(response: Response, value: str) -> None:
        response.set_cookie(
            COOKIE,
            value,
            httponly=True,
            secure=runtime.settings.auth_cookie_secure,
            samesite="lax",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"

    def me_value(request: Request) -> dict[str, Any]:
        subject = principal(request)
        if not service.enabled:
            return {
                "id": subject.user_id,
                "display_name": "本地管理员",
                "username": subject.user_id,
                "global_role": "super_admin",
                "auth_mode": subject.auth_mode,
                "must_change_password": False,
                "auth_revision": 0,
                "spaces": [],
                "capabilities": ["manage", "admin", "ask"],
            }
        row = service.user(subject.user_id)
        spaces = (
            []
            if row["must_change_password"]
            else service.allowed_spaces(subject, include_deleted=True)
        )
        return {
            **service.public_user(row),
            "auth_mode": "password",
            "auth_revision": row["auth_revision"],
            "spaces": spaces,
            "capabilities": (
                ["admin", "manage", "ask"]
                if row["global_role"] == "super_admin"
                else ["manage", "ask"]
                if any(s["my_role"] == "manager" for s in spaces)
                else ["ask"]
            ),
        }

    @router.get("/api/auth/csrf")
    def csrf(request: Request, response: Response) -> dict[str, Any]:
        if not service.enabled:
            return {"csrf_token": "", "auth_mode": runtime.settings.auth_mode}
        token = request.cookies.get(COOKIE, "")
        try:
            service.session(token)
            csrf_token = digest("csrf:" + token)
        except AuthenticationError:
            token, csrf_token = service.new_session()
        cookie(response, token)
        return {"csrf_token": csrf_token, "auth_mode": "password"}

    @router.post("/api/auth/login")
    def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
        active()
        # Never trust a caller-supplied forwarded address. Proxy trust belongs in deployment.
        address = request.client.host if request.client else "unknown"
        if runtime.settings.app_env != "testing":
            from redis.exceptions import RedisError

            try:
                address_ok = limiter.allow(
                    "login-ip", digest(service.tenant_id + address), limit=40, window_seconds=900
                )
                account_ok = limiter.allow(
                    "login-account",
                    digest(service.tenant_id + body.username.strip().lower()),
                    limit=10,
                    window_seconds=900,
                )
            except RedisError as error:
                raise HTTPException(503, "LOGIN_LIMITER_UNAVAILABLE") from error
            if not address_ok or not account_ok:
                raise HTTPException(429, "LOGIN_RATE_LIMITED", headers={"Retry-After": "900"})
        with service.guard():
            user_id = service.credentials(body.username, body.password)
            # Rotate the pre-login session; an attacker cannot fix the authenticated ID.
            with service.db.transaction() as connection:
                service.db.execute(
                    connection,
                    "UPDATE auth_sessions SET revoked=1 WHERE token_hash=?",
                    (digest(request.cookies.get(COOKIE, "")),),
                )
                service.audit(connection, user_id, "session.login", user_id)
            token, csrf_token = service.new_session(user_id)
        cookie(response, token)
        request.state.principal = service.authenticate(token)
        return {"user": me_value(request), "csrf_token": csrf_token}

    @router.get("/api/auth/me")
    def me(request: Request, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return me_value(request)

    @router.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        active()
        service.logout(request.cookies.get(COOKIE, ""))
        response.delete_cookie(COOKIE, path="/")
        return {"logged_out": True}

    @router.post("/api/auth/password")
    def password(body: PasswordBody, request: Request, response: Response) -> dict[str, Any]:
        active()
        service.change_password(principal(request), body.current_password, body.new_password)
        response.delete_cookie(COOKIE, path="/")
        return {"changed": True, "login_required": True}

    @router.get("/api/admin/users")
    def users(
        request: Request,
        q: str = "",
        enabled: int | None = Query(None, ge=0, le=1),
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
    ) -> dict[str, Any]:
        active()
        service.require_super(principal(request))
        filters, args = ["tenant_id=?"], [service.tenant_id]
        if q:
            filters.append("(username LIKE ? OR display_name LIKE ?)")
            args.extend(["%" + q[:100] + "%"] * 2)
        if enabled is not None:
            filters.append("enabled=?")
            args.append(str(enabled))
        where = " AND ".join(filters)
        with service.db.connection() as connection:
            rows = service.db.rows(
                connection,
                "SELECT * FROM auth_users WHERE "  # noqa: S608 - fixed clauses, bound values
                + where
                + " ORDER BY username,id LIMIT ? OFFSET ?",
                (*args, limit + 1, offset),
            )
        return {
            "items": [service.public_user(r) for r in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    @router.post("/api/admin/users", status_code=201)
    def create(body: CreateUser, request: Request) -> dict[str, Any]:
        active()
        return service.create_user(
            principal(request), body.username, body.display_name, body.global_role
        )

    @router.patch("/api/admin/users/{user_id}")
    def update(
        user_id: str, body: UpdateUser, request: Request, condition: str = Header(alias="If-Match")
    ) -> dict[str, Any]:
        active()
        return service.update_user(
            principal(request), user_id, body.model_dump(exclude_none=True), if_match(condition)
        )

    @router.post("/api/admin/users/{user_id}:reset-password")
    def reset(
        user_id: str, request: Request, condition: str = Header(alias="If-Match")
    ) -> dict[str, Any]:
        active()
        value = service.reset_password(principal(request), user_id, if_match(condition))
        return {
            "temporary_password": value,
            "expires_in": runtime.settings.auth_temporary_password_seconds,
        }

    @router.get("/api/admin/users/{user_id}/spaces")
    def user_spaces(user_id: str, request: Request) -> dict[str, Any]:
        active()
        service.require_super(principal(request))
        service.user(user_id)
        existing = {m["space_id"]: m["role"] for m in service.memberships(user_id)}
        return {
            "items": [
                {**s, "assigned_role": existing.get(s["id"], "none")}
                for s in service.allowed_spaces(principal(request), include_deleted=True)
            ]
        }

    @router.put("/api/admin/users/{user_id}/spaces")
    def assign(
        user_id: str,
        body: UserSpaceChange,
        request: Request,
        condition: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        active()
        service.require_super(principal(request))
        return service.set_members(
            principal(request),
            body.space_id,
            [{"user_id": user_id, "role": body.role}],
            if_match(condition),
        )

    @router.get("/api/spaces/{space_id}/members")
    def members(space_id: str, request: Request, response: Response) -> dict[str, Any]:
        active()
        service.require_space(principal(request), space_id, manage=True)
        with service.db.connection() as connection:
            rows = service.db.rows(
                connection,
                "SELECT m.user_id,m.role,m.created_at,m.assigned_by,"
                "u.username,u.display_name,u.enabled FROM space_memberships m "
                "JOIN auth_users u ON u.id=m.user_id AND u.tenant_id=m.tenant_id "
                "WHERE m.tenant_id=? AND m.space_id=? ORDER BY u.username",
                (service.tenant_id, space_id),
            )
        state = service.space_state(space_id)
        response.headers["ETag"] = f'"{state["row_version"]}"'
        return {"items": rows, "row_version": state["row_version"]}

    @router.get("/api/spaces/{space_id}/member-candidates")
    def candidates(
        space_id: str, request: Request, q: str = Query(min_length=2, max_length=100)
    ) -> list[dict[str, Any]]:
        active()
        service.require_space(principal(request), space_id, manage=True)
        with service.db.connection() as connection:
            return service.db.rows(
                connection,
                "SELECT id,username,display_name FROM auth_users "
                "WHERE tenant_id=? AND enabled=1 AND global_role='member' AND "
                "(username LIKE ? OR display_name LIKE ?) ORDER BY username LIMIT 20",
                (service.tenant_id, "%" + q + "%", "%" + q + "%"),
            )

    @router.put("/api/spaces/{space_id}/members")
    def change_members(
        space_id: str,
        body: MemberChanges,
        request: Request,
        condition: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        active()
        return service.set_members(
            principal(request),
            space_id,
            [c.model_dump() for c in body.changes],
            if_match(condition),
        )

    @router.post("/api/spaces/{space_id}:delete")
    def delete(
        space_id: str,
        request: Request,
        condition: str = Header(alias="If-Match"),
        key: str = Header(alias="Idempotency-Key", min_length=1, max_length=191),
    ) -> dict[str, Any]:
        active()
        return service.set_deleted(principal(request), space_id, True, if_match(condition), key)

    @router.post("/api/spaces/{space_id}:restore")
    def restore(
        space_id: str,
        request: Request,
        condition: str = Header(alias="If-Match"),
        key: str = Header(alias="Idempotency-Key", min_length=1, max_length=191),
    ) -> dict[str, Any]:
        active()
        return service.set_deleted(principal(request), space_id, False, if_match(condition), key)

    def audit_list(space: str, offset: int, limit: int) -> dict[str, Any]:
        with service.db.connection() as connection:
            rows = service.db.rows(
                connection,
                "SELECT * FROM account_audit_events WHERE tenant_id=? "  # noqa: S608
                + ("AND space_id=? " if space else "")
                + "ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (service.tenant_id, *((space,) if space else ()), limit + 1, offset),
            )
        items = []
        for row in rows[:limit]:
            detail = json.loads(row.pop("detail_json"))
            items.append(
                {
                    **row,
                    "detail": detail,
                    "actor_name": identity_name(row["actor_id"]),
                    "target_name": identity_name(row["target_id"]),
                }
            )
        return {
            "items": items,
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    def identity_name(identity: str) -> str:
        try:
            user = service.user(identity)
            return f"{user['display_name']}（{user['username']}）"
        except ResourceNotFoundError:
            try:
                return str(runtime.repository.get_space(identity)["name"])
            except ResourceNotFoundError:
                return identity

    @router.get("/api/spaces/{space_id}/usage")
    def space_usage(space_id: str, request: Request) -> dict[str, Any]:
        active()
        service.require_space(principal(request), space_id, manage=True)
        from ragkb.infrastructure import visual_usage
        from ragkb.infrastructure.visual_assets import VisualAssetStore

        with service.db.connection() as connection:
            if service.db.mysql:
                versions = service.db.rows(
                    connection,
                    "SELECT v.entity_id id FROM upload_entities v JOIN upload_entities d "
                    "ON v.parent_id=d.entity_id AND v.tenant_id=d.tenant_id "
                    "WHERE v.tenant_id=? AND v.entity_type='versions' "
                    "AND d.entity_type='documents' "
                    "AND JSON_UNQUOTE(JSON_EXTRACT(d.payload_json,'$.space_id'))=?",
                    (service.tenant_id, space_id),
                )
            else:
                versions = service.db.rows(
                    connection,
                    "SELECT v.id FROM document_versions v JOIN documents d ON d.id=v.document_id "
                    "JOIN sources s ON s.id=d.source_id JOIN corpora c ON c.id=s.corpus_id "
                    "WHERE c.space_id=?",
                    (space_id,),
                )
            conversations = service.db.one(
                connection,
                "SELECT COUNT(*) total FROM conversations WHERE tenant_id=? AND space_id=?",
                (service.tenant_id, space_id),
            )
        ledger = VisualAssetStore(runtime.storage).ledger
        total = dict.fromkeys(visual_usage.FIELDS, 0.0)
        with ledger.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            visual_usage.rollup(connection)
            for version in versions:
                for key, value in visual_usage.totals(connection, version["id"]).items():
                    total[key] += value or 0
            qa_usage = visual_usage.totals(connection, "space:" + space_id)
        return {
            "visual_processing": total,
            "conversation_count": conversations["total"] if conversations else 0,
            "scope": "library_lifetime",
            "qa_token_usage": qa_usage if runtime.settings.model_usage_enabled else None,
            "note": "图片用量包含识别、复核和看图补查；问答用量从本次升级后开始按库记录。"
            "旧用量不猜测归属，未定价调用不计为免费。",
        }

    @router.get("/api/admin/audit-events" if service.enabled else "/api/admin/account-audit-events")
    def audits(
        request: Request, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)
    ) -> dict[str, Any]:
        active()
        service.require_super(principal(request))
        return audit_list("", offset, limit)

    @router.get("/api/spaces/{space_id}/audit-events")
    def space_audits(
        space_id: str,
        request: Request,
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
    ) -> dict[str, Any]:
        active()
        service.require_space(principal(request), space_id, manage=True)
        return audit_list(space_id, offset, limit)

    @router.get("/api/admin/conversation-audits")
    def conversations(
        request: Request,
        space_id: str = "",
        user_id: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
    ) -> dict[str, Any]:
        active()
        service.require_super(principal(request))
        filters, values = ["tenant_id=?"], [service.tenant_id]
        for key, value in (("space_id", space_id), ("user_id", user_id)):
            if value:
                filters.append(key + "=?")
                values.append(value)
        with service.db.connection() as connection:
            rows = service.db.rows(
                connection,
                "SELECT id,user_id,space_id,created_at,updated_at "  # noqa: S608
                "FROM conversations WHERE "
                + " AND ".join(filters)
                + " ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?",
                (*values, limit + 1, offset),
            )
        # No titles or questions before the explicit audited read.
        return {
            "items": [
                {
                    **row,
                    "username": identity_name(row["user_id"]),
                    "space_name": identity_name(row["space_id"]),
                }
                for row in rows[:limit]
            ],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    @router.post("/api/admin/conversation-audits/{conversation_id}:read")
    def audit_conversation(
        conversation_id: str,
        body: AuditRead,
        request: Request,
        response: Response,
        before: int | None = Query(None, ge=1),
    ) -> dict[str, Any]:
        active()
        subject = principal(request)
        with service.guard(), service.db.transaction() as connection:
            service.require_super(subject, connection)
            row = service.db.one(
                connection,
                "SELECT * FROM conversations WHERE id=? AND tenant_id=?",
                (conversation_id, service.tenant_id),
            )
            if not row:
                raise ResourceNotFoundError(conversation_id)
            turns = service.db.rows(
                connection,
                "SELECT original_question,result_json,sequence_number,"  # noqa: S608
                "created_at,state FROM conversation_turns WHERE conversation_id=? "
                + ("AND sequence_number<? " if before else "")
                + "ORDER BY sequence_number DESC LIMIT 51",
                (conversation_id, *((before,) if before else ())),
            )
            service.audit(
                connection,
                subject.user_id,
                "conversation.audit_read",
                conversation_id,
                row["space_id"],
                {"reason": body.reason.strip(), "owner_id": row["user_id"]},
            )
        response.headers["Cache-Control"] = "no-store"
        entries = []
        for turn in reversed(turns[:50]):
            result = json.loads(turn.pop("result_json") or "null")
            entries.append(
                {
                    **turn,
                    "answer": result.get("answer") if result else None,
                    "verified_at_generation": bool(result and result.get("verified")),
                }
            )
        return {
            "conversation": {k: row[k] for k in ("id", "user_id", "space_id", "title")},
            "turns": entries,
            "historical_audit": True,
            "next_before": turns[49]["sequence_number"] if len(turns) > 50 else None,
        }

    return router
