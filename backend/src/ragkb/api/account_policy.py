"""Closed route policy for password mode; URLs and resource ownership are checked server-side."""

from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request

from ragkb.adapters.auth import AuthenticationError, AuthorizationError
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.uploads import ResourceNotFoundError
from ragkb.infrastructure.accounts import AccountService, digest

AUTH_PUBLIC = {"/api/auth/csrf", "/api/auth/login"}
AUTH_ROUTES = AUTH_PUBLIC | {"/api/auth/me", "/api/auth/logout", "/api/auth/password"}


def check_csrf(service: AccountService, request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("Origin", "")
    expected = str(request.base_url).rstrip("/")
    if origin not in {*service.settings.cors_origins, expected} or urlsplit(origin).scheme not in {
        "http",
        "https",
    }:
        raise AuthorizationError("CSRF_ORIGIN_REJECTED")
    session = service.session(request.cookies.get("ragkb_session", ""))
    token = request.headers.get("X-CSRF-Token", "")
    if not token or not secrets.compare_digest(digest(token), session["csrf_hash"]):
        raise AuthorizationError("CSRF_TOKEN_INVALID")


async def authorize_route(
    service: AccountService, runtime: Any, request: Request, subject: RequestPrincipal
) -> str:
    path = request.url.path
    if path in AUTH_ROUTES:
        return ""
    user = service.user(subject.user_id)
    if user["must_change_password"]:
        raise AuthorizationError("PASSWORD_CHANGE_REQUIRED")
    if path in {"/docs", "/openapi.json", "/docs/oauth2-redirect"}:
        service.require_super(subject)
        return ""
    route_path, params = "", {}
    registry = getattr(request.app.state, "account_route_registry", None)
    if registry is None:
        from starlette.routing import compile_path

        registry = [
            (p, methods, compile_path(p)[0])
            for p, methods in request.app.openapi()["paths"].items()
        ]
        request.app.state.account_route_registry = registry
    for template, methods, pattern in registry:
        matched = pattern.fullmatch(path)
        if matched and request.method.lower() in methods:
            route_path, params = template, matched.groupdict()
            break
    if not route_path:
        raise ResourceNotFoundError(path)
    if (
        path.startswith("/api/admin/")
        or path.startswith("/api/governance/")
        or path.startswith("/status/")
        or path == "/api/system/status"
    ):
        service.require_super(subject)
        return ""
    if path == "/api/capabilities":
        return ""
    if path in {"/api/spaces", "/api/spaces/overview"}:
        if request.method != "GET":
            service.require_super(subject)
        return ""
    if path in {"/api/ingestion-jobs", "/api/ingestion-jobs/summary"}:
        if not subject.has_role("admin", "knowledge_maintainer"):
            raise AuthorizationError("MANAGER_REQUIRED")
        if request.query_params.get("space_id"):
            service.require_space(subject, request.query_params["space_id"], manage=True)
        return ""
    if path == "/api/conversations":
        if request.method == "POST":
            body = await request.json()
            service.require_space(subject, str(body.get("space_id", "")))
        elif request.query_params.get("space_id"):
            service.require_space(subject, request.query_params["space_id"])
        return ""
    if path.startswith("/api/conversations/"):
        with service.db.connection() as connection:
            row = service.db.one(
                connection,
                "SELECT space_id FROM conversations WHERE id=? AND tenant_id=? AND user_id=?",
                (params.get("identity"), service.tenant_id, subject.user_id),
            )
        if not row:
            raise ResourceNotFoundError(path)
        service.require_space(subject, row["space_id"])
        return str(row["space_id"])
    if path in {"/api/ask", "/api/ask:stream", "/api/search"}:
        body = await request.json()
        space = str(body.get("space_id") or "")
        if not space:
            raise AuthorizationError("EXPLICIT_SPACE_REQUIRED")
        service.require_space(subject, space, manage=path == "/api/search")
        return space
    if path.startswith("/api/rag-runs/"):
        if "run_token" in params:
            run_id, evidence_id = runtime.reference_signer.resolve(
                params["run_token"], params["evidence_token"], subject.tenant_id, subject.user_id
            )
            evidence = runtime.rag_repository.get_evidence(run_id, evidence_id)
            result = runtime.rag_repository.get_result(run_id)
            if (
                not evidence
                or not result
                or not result.verified
                or evidence_id not in {c.evidence_id for c in result.citations}
            ):
                raise ResourceNotFoundError(path)
            space = runtime.repository.get_document_space(evidence.document_id)
            service.require_space(subject, space)
            return str(space)
        package = runtime.rag_repository.get_package(params.get("rag_run_id", ""))
        if (
            not package
            or package.user_id != subject.user_id
            or package.tenant_id != subject.tenant_id
        ):
            raise ResourceNotFoundError(path)
        for evidence in package.evidence:
            service.require_space(
                subject, runtime.repository.get_document_space(evidence.document_id)
            )
        return ""
    space = str(params.get("space_id", ""))
    document = str(params.get("document_id", ""))
    version = str(params.get("version_id", ""))
    if version:
        document = str(runtime.repository.get_version(version)["document_id"])
    if document:
        document_space = runtime.repository.get_document_space(document)
        if space and document_space != space:
            raise ResourceNotFoundError(path)
        space = document_space
    if "session_id" in params:
        space = runtime.repository.get_session(params["session_id"]).space_id
    if "job_id" in params:
        job = runtime.queue.get(params["job_id"])
        if not job or job.payload.get("tenant_id") != service.tenant_id:
            raise ResourceNotFoundError(path)
        space = str(job.payload.get("space_id", ""))
    if space:
        service.require_space(
            subject, space, manage=True, include_deleted=path.endswith((":delete", ":restore"))
        )
        return space
    # A newly introduced API is not accidentally public or a reader capability.
    service.require_super(subject)
    return ""


def validate_transport(service: AccountService, request: Request) -> None:
    if service.settings.app_env == "testing" and request.url.hostname == "testserver":
        return
    if request.url.scheme != "https" and request.url.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise AuthenticationError("PASSWORD_AUTH_REQUIRES_HTTPS")
