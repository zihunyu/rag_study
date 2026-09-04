"""FastAPI application factory, middleware, and global exception policy."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from ragkb.adapters.auth import AuthenticationError, AuthorizationError
from ragkb.api.routers.documents import build_documents_router
from ragkb.api.routers.governance import build_governance_router
from ragkb.api.routers.health import build_health_router
from ragkb.api.routers.lifecycle import build_lifecycle_router
from ragkb.api.routers.rag import build_rag_router
from ragkb.api.routers.spaces import build_spaces_router
from ragkb.api.routers.uploads import build_uploads_router
from ragkb.api.support import error_response as _error
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime.settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.components = runtime

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        if request.url.path not in {"/health/live", "/health/ready", "/docs", "/openapi.json"}:
            try:
                request.state.principal = runtime.authenticator.authenticate(
                    request.headers.get("Authorization")
                )
            except AuthenticationError:
                return _error(
                    request,
                    "AUTHENTICATION_REQUIRED",
                    "authentication is required",
                    401,
                )
        response = await call_next(request)
        runtime.observability.request_completed(
            request.state.request_id, request.method, request.url.path, response.status_code
        )
        response.headers["X-Request-ID"] = request.state.request_id
        return response

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

    app.include_router(build_health_router(runtime))
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
        for path, methods in schema.get("paths", {}).items():
            if path.startswith("/health/"):
                continue
            for operation in methods.values():
                if isinstance(operation, dict) and "responses" in operation:
                    operation["security"] = [{"BearerAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    return app
