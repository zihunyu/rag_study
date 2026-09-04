"""Health API routes."""

from __future__ import annotations

import shutil
import time

from fastapi import APIRouter, Response, status

from ragkb.api.models import (
    HealthResponse,
)
from ragkb.domain.retrieval import SearchContext
from ragkb.runtime_components import RuntimeComponents

OPENAPI_VERSION = "1.0.0"


def build_health_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter()

    def cached_runtime_health() -> tuple[dict[str, object], list[str]]:
        dependencies: dict[str, object] = {}
        degraded: list[str] = []
        free_bytes = shutil.disk_usage(runtime.storage.root).free
        minimum_bytes = int(runtime.settings.local_storage_min_free_gb * 1024**3)
        dependencies["storage"] = {
            "state": "ready" if free_bytes >= minimum_bytes else "insufficient_space",
            "free_bytes": free_bytes,
            "minimum_free_bytes": minimum_bytes,
        }
        if free_bytes < minimum_bytes:
            degraded.append("LOCAL_STORAGE_FREE_SPACE_LOW")
        roles = ("embedding", "reranker", "generator", "verifier")
        providers: dict[str, object] = {}
        for role, transport in zip(roles, runtime.provider_transports, strict=False):
            snapshot = transport.health_snapshot()
            providers[role] = snapshot
            if snapshot["circuit_open"]:
                degraded.append(f"{role.upper()}_CIRCUIT_OPEN")
        dependencies["providers"] = providers or {"state": "local_not_applicable"}
        return dependencies, degraded

    @router.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def live() -> HealthResponse:
        return HealthResponse(
            status="ok",
            runtime="g3_native_python",
            real_service_acceptance=runtime.search_service.real_acceptance,
        )

    @router.get("/health/ready", response_model=HealthResponse, tags=["health"])
    async def ready(response: Response) -> HealthResponse:
        dependencies, degraded = cached_runtime_health()
        if runtime.settings.rag_runtime_profile == "production":
            try:
                runtime.repository.list_spaces()
                dependencies["mysql"] = "ready"
            except Exception:
                dependencies["mysql"] = "unavailable"
                degraded.append("MYSQL_UNAVAILABLE")
            try:
                runtime.queue.get("__readiness_probe__")
                dependencies["queue"] = "ready"
            except Exception:
                dependencies["queue"] = "unavailable"
                degraded.append("REDIS_QUEUE_UNAVAILABLE")
            try:
                release = runtime.retrieval_release.current_release(
                    runtime.tenant_id, runtime.space_id
                )
                probe_context = SearchContext(
                    runtime.tenant_id,
                    (runtime.space_id,),
                    (),
                    0,
                    int(time.time()),
                    release.active_generation_id,
                    release.active_permission_revision,
                    release.security_watermark,
                )
                runtime.search_service.index.observed_security_watermark(probe_context)
                dependencies["retrieval_release"] = "ready"
            except Exception:
                dependencies["retrieval_release"] = "unavailable"
                degraded.append("RETRIEVAL_RELEASE_UNAVAILABLE")
        else:
            runtime.database.initialize()
            dependencies["sqlite"] = "ready"
        if degraded:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="ready" if not degraded else "not_ready",
            runtime="g3_native_python",
            real_service_acceptance=runtime.search_service.real_acceptance,
            dependencies=dependencies,
            degraded_reasons=degraded,
        )

    @router.get("/status/acceptance", response_model=HealthResponse, tags=["health"])
    async def acceptance_status() -> HealthResponse:
        return HealthResponse(
            status="accepted" if runtime.search_service.real_acceptance else "not_accepted",
            runtime="g3_native_python",
            real_service_acceptance=runtime.search_service.real_acceptance,
        )

    @router.get("/status/degraded", response_model=HealthResponse, tags=["health"])
    async def degraded_status() -> HealthResponse:
        dependencies, degraded = cached_runtime_health()
        return HealthResponse(
            status="degraded" if degraded else "ok",
            runtime="g3_native_python",
            real_service_acceptance=runtime.search_service.real_acceptance,
            dependencies=dependencies,
            degraded_reasons=degraded,
        )

    return router
