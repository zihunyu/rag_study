"""FastAPI application factory, middleware, and global exception policy."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ragkb.adapters.auth import AuthenticationError, AuthorizationError
from ragkb.adapters.conversation_context import LocalContextResolver, ModelContextResolver
from ragkb.api.routers.conversations import build_conversations_router
from ragkb.api.routers.documents import build_documents_router
from ragkb.api.routers.governance import build_governance_router
from ragkb.api.routers.health import build_health_router
from ragkb.api.routers.lifecycle import build_lifecycle_router
from ragkb.api.routers.rag import build_rag_router
from ragkb.api.routers.spaces import build_spaces_router
from ragkb.api.routers.uploads import build_uploads_router
from ragkb.api.routers.workspace import build_workspace_router, workspace_queries
from ragkb.api.support import error_response as _error
from ragkb.application.access_telemetry import AccessTelemetry
from ragkb.application.lifecycle import (
    CleanupApprovalRequired,
    LifecycleIdempotencyConflict,
    LifecycleStateConflict,
)
from ragkb.application.uploads import MalwareRejectedError, UploadStateError
from ragkb.contracts.jobs import QueueConflictError, QueueLeaseError, QueueStateError
from ragkb.domain.retrieval import SecurityWatermarkNotReady
from ragkb.domain.uploads import (
    IdempotencyConflictError,
    OptimisticConcurrencyError,
    ResourceNotFoundError,
)
from ragkb.engineering_security.file_validation import FileValidationError
from ragkb.engineering_security.references import ReferenceTokenError
from ragkb.infrastructure.conversation_service import ConversationService
from ragkb.infrastructure.conversations import ConversationRepository
from ragkb.runtime_components import RuntimeComponents, build_runtime_components

OPENAPI_VERSION = "1.0.0"


def create_app(components: RuntimeComponents | None = None) -> FastAPI:
    runtime = components or build_runtime_components()
    app = FastAPI(
        title=runtime.settings.app_name,
        version=OPENAPI_VERSION,
        openapi_version="3.1.0",
        docs_url="/docs",
        debug=runtime.settings.app_debug,
    )
    access_metrics = AccessTelemetry()
    app.state.access_metrics = access_metrics
    queries = workspace_queries(runtime)
    from ragkb.api.account_policy import (
        AUTH_PUBLIC,
        authorize_route,
        check_csrf,
        validate_transport,
    )
    from ragkb.api.routers.accounts import build_accounts_router
    from ragkb.infrastructure.accounts import AccountRuleError, AccountService

    accounts = runtime.accounts or AccountService(
        queries.db, runtime.tenant_id, runtime.settings, runtime.repository
    )
    app.state.accounts = accounts
    conversation_service = ConversationService(
        runtime,
        ConversationRepository(queries.db),
        ModelContextResolver(
            runtime.settings,
            runtime.provider_transports[2]
            if len(runtime.provider_transports) > 2
            else runtime.model_transport,
        )
        if runtime.settings.rag_runtime_profile == "production"
        else LocalContextResolver(),
        queries,
    )
    app.state.conversation_service = conversation_service
    app.router.add_event_handler("shutdown", conversation_service.close)
    app.router.add_event_handler("shutdown", access_metrics.close)
    for provider_transport in runtime.provider_transports:
        app.router.add_event_handler("shutdown", provider_transport.close)
    mysql_control = getattr(runtime.repository, "control", None)
    close_mysql_pool = getattr(mysql_control, "close", None)
    if callable(close_mysql_pool):
        app.router.add_event_handler("shutdown", close_mysql_pool)
    oidc_decoder = getattr(runtime.authenticator, "verified_decoder", None)
    close_oidc_decoder = getattr(oidc_decoder, "close", None)
    if callable(close_oidc_decoder):
        app.router.add_event_handler("shutdown", close_oidc_decoder)
    app.state.components = runtime

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request_space = ""
        if accounts.enabled and request.url.path not in {"/health/live", "/health/ready"}:
            try:
                validate_transport(accounts, request)
                await run_in_threadpool(check_csrf, accounts, request)
                if request.url.path not in AUTH_PUBLIC:
                    request.state.principal = await run_in_threadpool(
                        accounts.authenticate,
                        request.cookies.get("ragkb_session", ""),
                        touch=request.url.path not in {"/api/auth/me", "/api/auth/csrf"},
                    )
                    request_space = await authorize_route(
                        accounts, runtime, request, request.state.principal
                    )
                    request.state.account_space = request_space
            except AuthenticationError:
                return _error(request, "AUTHENTICATION_REQUIRED", "登录信息无效或已过期", 401)
            except AuthorizationError as error:
                return _error(request, str(error), "当前账号不能执行此操作", 403)
            except ResourceNotFoundError:
                return _error(request, "NOT_FOUND", "没有找到可访问的资源", 404)
            except ReferenceTokenError:
                return _error(request, "SOURCE_REFERENCE_NOT_FOUND", "没有找到可访问的引用", 404)
            except (ValueError, TypeError):
                return _error(request, "REQUEST_INVALID", "请求内容格式不正确", 422)
        elif request.url.path not in {
            "/health/live",
            "/health/ready",
            "/docs",
            "/openapi.json",
            *AUTH_PUBLIC,
        }:
            try:
                request.state.principal = await run_in_threadpool(
                    runtime.authenticator.authenticate, request.headers.get("Authorization")
                )
            except AuthenticationError:
                return _error(
                    request,
                    "AUTHENTICATION_REQUIRED",
                    "authentication is required",
                    401,
                )
        response = await call_next(request)
        if accounts.enabled:
            response.headers["Cache-Control"] = "no-store"
            if (
                request_space
                and request.method not in {"GET", "HEAD", "OPTIONS"}
                and response.status_code < 400
                and not request.url.path.startswith(
                    ("/api/conversations", "/api/ask", "/api/search", "/api/rag-runs")
                )
            ):

                def audit_operation() -> None:
                    with accounts.db.transaction() as connection:
                        accounts.audit(
                            connection,
                            request.state.principal.user_id,
                            "resource." + request.method.lower(),
                            request.url.path,
                            request_space,
                        )

                await run_in_threadpool(audit_operation)
        if not request.url.path.startswith("/health/"):
            access_metrics.submit(
                runtime.observability.request_completed,
                request.state.request_id,
                request.method,
                request.url.path,
                response.status_code,
            )
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    # Outermost middleware handles unauthenticated browser preflight and error responses.
    from ragkb.api.account_release import AccountReleaseMiddleware

    app.add_middleware(AccountReleaseMiddleware, service=accounts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime.settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
        expose_headers=["ETag", "X-Request-ID", "X-Next-Cursor", "Retry-After"],
    )

    @app.exception_handler(AccountRuleError)
    async def account_rule(request: Request, error: AccountRuleError) -> JSONResponse:
        return _error(request, str(error), "请检查输入或刷新当前状态", 409)

    @app.exception_handler(ResourceNotFoundError)
    async def not_found(request: Request, error: ResourceNotFoundError) -> JSONResponse:
        return _error(request, "NOT_FOUND", "resource was not found", 404)

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict(request: Request, error: Exception) -> JSONResponse:
        return _error(request, "CONFLICT_IDEMPOTENCY_KEY", str(error), 409)

    app.add_exception_handler(QueueConflictError, idempotency_conflict)
    app.add_exception_handler(LifecycleIdempotencyConflict, idempotency_conflict)

    @app.exception_handler(LifecycleStateConflict)
    async def lifecycle_conflict(request: Request, error: LifecycleStateConflict) -> JSONResponse:
        return _error(request, "CONFLICT_LIFECYCLE_STATE", str(error), 409)

    @app.exception_handler(CleanupApprovalRequired)
    async def cleanup_blocked(request: Request, error: CleanupApprovalRequired) -> JSONResponse:
        return _error(request, "CLEANUP_PENDING_APPROVAL", str(error), 409)

    @app.exception_handler(OptimisticConcurrencyError)
    async def concurrency(request: Request, error: OptimisticConcurrencyError) -> JSONResponse:
        return _error(request, "CONFLICT_ETAG", str(error), 412)

    @app.exception_handler(UploadStateError)
    async def state_conflict(request: Request, error: Exception) -> JSONResponse:
        return _error(request, "CONFLICT_STATE", str(error), 409)

    app.add_exception_handler(QueueLeaseError, state_conflict)
    app.add_exception_handler(QueueStateError, state_conflict)

    @app.exception_handler(TimeoutError)
    async def request_timeout(request: Request, error: TimeoutError) -> JSONResponse:
        return _error(request, "REQUEST_TIMEOUT", "request deadline exceeded", 408)

    @app.exception_handler(FileValidationError)
    async def invalid_file(request: Request, error: FileValidationError) -> JSONResponse:
        if error.code == "DOC_SIZE_LIMIT":
            return _error(request, error.code, str(error), 413)
        if error.code == "UPLOAD_QUARANTINE_QUOTA_EXCEEDED":
            return _error(request, error.code, str(error), 507, retryable=True)
        return _error(request, error.code, str(error), 422)

    @app.exception_handler(SecurityWatermarkNotReady)
    async def watermark_not_ready(
        request: Request, error: SecurityWatermarkNotReady
    ) -> JSONResponse:
        return _error(
            request,
            "SECURITY_WATERMARK_NOT_READY",
            "retrieval permission projection is not ready",
            503,
            retryable=True,
        )

    @app.exception_handler(ReferenceTokenError)
    async def invalid_reference(request: Request, error: ReferenceTokenError) -> JSONResponse:
        return _error(request, "SOURCE_REFERENCE_NOT_FOUND", "source was not found", 404)

    @app.exception_handler(AuthenticationError)
    async def authentication_error(request: Request, error: AuthenticationError) -> JSONResponse:
        return _error(request, "AUTHENTICATION_REQUIRED", "authentication is required", 401)

    @app.exception_handler(AuthorizationError)
    async def authorization_error(request: Request, error: AuthorizationError) -> JSONResponse:
        return _error(request, "FORBIDDEN", "operation is forbidden", 403)

    @app.exception_handler(MalwareRejectedError)
    async def malware(request: Request, error: MalwareRejectedError) -> JSONResponse:
        return _error(request, error.reason_code, "file was rejected by malware policy", 422)

    app.include_router(build_accounts_router(runtime, accounts))
    app.include_router(build_health_router(runtime))
    app.include_router(build_workspace_router(runtime))
    app.include_router(build_conversations_router(runtime, conversation_service))
    app.include_router(build_spaces_router(runtime))
    app.include_router(build_uploads_router(runtime))
    app.include_router(build_documents_router(runtime))
    app.include_router(build_rag_router(runtime))
    app.include_router(build_lifecycle_router(runtime))
    app.include_router(build_governance_router(runtime))

    def custom_openapi() -> dict[str, object]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
        security_schemes["SessionCookie"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": "ragkb_session",
            "description": "HttpOnly server session; obtain a CSRF token before login or writes.",
        }
        for path, methods in schema.get("paths", {}).items():
            if path.startswith("/health/"):
                continue
            for method, operation in methods.items():
                if isinstance(operation, dict) and "responses" in operation:
                    operation["security"] = (
                        []
                        if path in AUTH_PUBLIC
                        else [{"SessionCookie": []}]
                        if accounts.enabled
                        else [{"BearerAuth": []}]
                    )
                    if accounts.enabled and method not in {"get", "head", "options"}:
                        operation.setdefault("parameters", []).append(
                            {
                                "name": "X-CSRF-Token",
                                "in": "header",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Token from /api/auth/csrf or successful login; "
                                "Origin is also checked.",
                            }
                        )
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    return app
