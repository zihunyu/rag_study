"""Health API routes."""

from __future__ import annotations

import shutil
import sqlite3
import time

from fastapi import APIRouter, Response, status

from ragkb.api.models import (
    HealthResponse,
)
from ragkb.domain.retrieval import SearchContext
from ragkb.engineering_security.file_validation import FORMAT_BY_EXTENSION
from ragkb.infrastructure.visual_assets import VisualAssetStore
from ragkb.infrastructure.worker_heartbeat import worker_status
from ragkb.runtime_components import RuntimeComponents

OPENAPI_VERSION = "1.0.0"


def build_health_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter()

    @router.get("/api/capabilities", tags=["health"])
    def capabilities() -> dict[str, object]:
        production = runtime.settings.rag_runtime_profile == "production"
        scanner = runtime.uploads.malware_scanner
        return {
            "profile": runtime.settings.rag_runtime_profile,
            "max_file_size_bytes": runtime.settings.upload_max_file_size_mb * 1024 * 1024,
            "accepted_extensions": [
                ext for ext, (kind, _) in FORMAT_BY_EXTENSION.items() if kind != "audio"
            ],
            "file_mime_types": {
                ext: mime for ext, (kind, mime) in FORMAT_BY_EXTENSION.items() if kind != "audio"
            },
            "scanner_mode": "production" if production else "development",
            "scanner_available": bool(getattr(scanner, "executable", None)) if production else True,
            "scanner_certified": False,
            "native_parser_isolated": production,
            "audio_available": False,
            "audio_reason": "ASR_PROVIDER_NOT_CONFIGURED"
            if production
            else "LOCAL_ASR_PLACEHOLDER",
            "mineru_configured": bool(runtime.settings.mineru_tokens),
        }

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
        try:
            worker = worker_status(
                VisualAssetStore(runtime.storage).ledger,
                stale_seconds=runtime.settings.worker_heartbeat_stale_seconds,
                stall_seconds=runtime.settings.worker_task_stall_seconds,
            )
        except (OSError, sqlite3.Error):
            worker = {"state": "unavailable", "reason": "无法读取处理进程心跳"}
        dependencies["worker"] = worker
        if worker["state"] == "unavailable":
            degraded.append("WORKER_HEARTBEAT_UNAVAILABLE")
        elif worker["state"] == "stalled":
            degraded.append("WORKER_TASK_STALLED")
        return dependencies, degraded

    @router.get("/health/live", response_model=HealthResponse, tags=["health"])
    def live() -> HealthResponse:
        return HealthResponse(
            status="ok",
            runtime="g3_native_python",
            real_service_acceptance=runtime.search_service.real_acceptance,
        )

    @router.get("/health/ready", response_model=HealthResponse, tags=["health"])
    def ready(response: Response) -> HealthResponse:
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

    @router.get("/api/system/status", tags=["health"])
    def workspace_status() -> dict[str, object]:
        # Reuse real readiness probes. Failed dependencies remain readable to the dashboard.
        snapshot = ready(Response()).model_dump()
        usage = VisualAssetStore(runtime.storage).ledger.usage_report()
        embedding_stats = getattr(runtime.search_service.embedding, "cache_stats", None)
        return {
            **snapshot,
            "checked_at": time.time(),
            "model_usage": {k: v for k, v in usage.items() if k != "records"},
            "embedding_cache": {
                "document_enabled": runtime.settings.embedding_cache_enabled,
                "query_enabled": runtime.settings.query_embedding_cache_enabled,
                "process_counters": embedding_stats() if callable(embedding_stats) else {},
            },
            "visual_processing": {
                "enabled": runtime.settings.ocr_enabled,
                "ocr_model": runtime.settings.ocr_model,
                "verifier_model": runtime.settings.ocr_verify_model
                if runtime.settings.ocr_verify_enabled
                else runtime.settings.ocr_model,
                "local_ocr": "RapidOCR / PP-OCRv4"
                if runtime.settings.ocr_local_check_enabled
                else "未启用",
                "account_limit_enabled": runtime.settings.model_account_limit_enabled,
                "account_concurrency": runtime.settings.model_account_max_concurrency,
                "render_fallback_enabled": runtime.settings.ocr_render_fallback_enabled,
            },
            "worker": snapshot["dependencies"]["worker"],
            "parser": {
                "state": "configured" if runtime.settings.mineru_tokens else "unconfigured",
                "reason": "配置状态；实际解析结果请查看文件处理任务",
            },
        }

    @router.get("/status/acceptance", response_model=HealthResponse, tags=["health"])
    def acceptance_status() -> HealthResponse:
        return HealthResponse(
            status="accepted" if runtime.search_service.real_acceptance else "not_accepted",
            runtime="g3_native_python",
            real_service_acceptance=runtime.search_service.real_acceptance,
        )

    @router.get("/status/degraded", response_model=HealthResponse, tags=["health"])
    def degraded_status() -> HealthResponse:
        dependencies, degraded = cached_runtime_health()
        return HealthResponse(
            status="degraded" if degraded else "ok",
            runtime="g3_native_python",
            real_service_acceptance=runtime.search_service.real_acceptance,
            dependencies=dependencies,
            degraded_reasons=degraded,
        )

    return router
