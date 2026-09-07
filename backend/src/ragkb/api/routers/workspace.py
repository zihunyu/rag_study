"""Knowledge workspace presentation and explicit review/publication workflow."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ragkb.api.models import DocumentReviewRequest, SecurityProjectionRequest
from ragkb.api.pagination import read_cursor, write_cursor
from ragkb.api.routers.documents import apply_document_review
from ragkb.api.routers.lifecycle import version_condition
from ragkb.api.support import (
    document_manager,
    ensure_document_previewable,
    lifecycle_response,
    principal,
    require_document_manager,
    require_local_tenant,
    require_role,
)
from ragkb.domain.state_machines import JobState
from ragkb.domain.uploads import IdempotencyConflictError, ResourceNotFoundError
from ragkb.infrastructure.workspace_db import WorkspaceDB
from ragkb.infrastructure.workspace_queries import WorkspaceQueries
from ragkb.runtime_components import RuntimeComponents


class SpaceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r".*\S.*")
    description: str = Field(default="", max_length=2000)


class PublicationConfirmation(BaseModel):
    comment: str = Field(default="", max_length=2000)


def workspace_queries(runtime: RuntimeComponents) -> WorkspaceQueries:
    return WorkspaceQueries(
        WorkspaceDB(runtime.database, getattr(runtime.repository, "control", None)),
        runtime.tenant_id,
        runtime.settings.retrieval_active_generation_id,
    )


def build_workspace_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter(tags=["workspace"])
    queries = workspace_queries(runtime)
    db = queries.db

    @router.get("/api/ingestion-jobs")
    def jobs(
        request: Request,
        response: Response,
        space_id: str = "",
        state: str = "",
        limit: int = Query(default=30, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=2048),
    ) -> list[dict[str, Any]]:
        subject = principal(request)
        require_local_tenant(runtime, subject)
        require_role(subject, "knowledge_maintainer", "admin")
        if state and state not in {item.value for item in JobState}:
            raise HTTPException(422, "UNKNOWN_JOB_STATE")
        if space_id and not document_manager(subject, space_id):
            raise ResourceNotFoundError(space_id)
        scope = f"jobs:{subject.tenant_id}:{space_id}:{state}"
        page = runtime.queue.list_jobs_page(
            subject.tenant_id, space_id, state, limit=limit, after=read_cursor(cursor, scope, 0)
        )
        write_cursor(response, scope, page.next_key)
        items = []
        for row in page.items:
            payload = row["payload"]
            job_space = str(payload.get("space_id", ""))
            if not document_manager(subject, job_space):
                continue
            document_id = str(payload.get("document_id", ""))
            details = (
                queries.documents(job_space, document_id=document_id, limit=1).items
                if document_id
                else []
            )
            version_id = str(payload.get("document_version_id", ""))
            try:
                version = runtime.repository.get_version(version_id)
            except ResourceNotFoundError:
                version = {}
            items.append(
                {
                    **{
                        key: row.get(key)
                        for key in (
                            "id",
                            "state",
                            "attempt",
                            "max_attempts",
                            "cancel_requested",
                            "error_code",
                            "created_at",
                            "updated_at",
                        )
                    },
                    "space_id": job_space,
                    "document_id": document_id,
                    "version_id": version_id,
                    "filename": details[0]["filename"]
                    if details
                    else payload.get("filename", "文件记录已不可用"),
                    "processing_state": version.get("processing_state"),
                    "available_actions": (
                        ["retry"]
                        if row["state"] in ("FAILED_FINAL", "CANCELLED")
                        else ["cancel"]
                        if row["state"] in ("QUEUED", "RUNNING", "RETRY_WAIT")
                        else []
                    ),
                }
            )
        return items

    @router.get("/api/ingestion-jobs/summary")
    def job_summary(request: Request, space_id: str = "") -> dict[str, Any]:
        subject = principal(request)
        require_local_tenant(runtime, subject)
        require_role(subject, "knowledge_maintainer", "admin")
        if space_id:
            if not document_manager(subject, space_id):
                raise ResourceNotFoundError(space_id)
            return {"counts": runtime.queue.job_counts(subject.tenant_id, space_id)}
        counts: dict[str, int] = {}
        for space in runtime.repository.list_spaces():
            if document_manager(subject, space["id"]):
                for key, value in runtime.queue.job_counts(subject.tenant_id, space["id"]).items():
                    counts[key] = counts.get(key, 0) + value
        return {"counts": counts}

    @router.get("/api/spaces/overview")
    def overview(request: Request) -> dict[str, Any]:
        subject = principal(request)
        require_local_tenant(runtime, subject)
        require_role(subject, "knowledge_maintainer", "admin")
        summaries, descriptions = queries.summaries(), queries.metadata()
        spaces = []
        for item in runtime.repository.list_spaces():
            if not document_manager(subject, item["id"]):
                continue
            counts = summaries.get(item["id"], {})
            spaces.append(
                {
                    **item,
                    "description": descriptions.get(item["id"], ""),
                    **{
                        key: counts.get(key, 0)
                        for key in (
                            "document_count",
                            "answerable_count",
                            "pending_count",
                            "processing_count",
                            "chunk_count",
                            "updated_ms",
                        )
                    },
                }
            )
        return {
            "items": spaces,
            "totals": {
                key: sum(item[key] for item in spaces)
                for key in (
                    "document_count",
                    "answerable_count",
                    "pending_count",
                    "processing_count",
                    "chunk_count",
                )
            },
        }

    @router.patch("/api/spaces/{space_id}")
    def update_space(space_id: str, body: SpaceUpdate, request: Request) -> dict[str, Any]:
        subject = principal(request)
        require_local_tenant(runtime, subject)
        require_role(subject, "knowledge_maintainer", "admin")
        item = runtime.repository.get_space(space_id)
        if item["tenant_id"] != subject.tenant_id or not document_manager(subject, space_id):
            raise ResourceNotFoundError(space_id)
        runtime.repository.rename_space(space_id, " ".join(body.name.split()))
        queries.set_description(space_id, body.description.strip())
        return {**runtime.repository.get_space(space_id), "description": body.description.strip()}

    @router.get("/api/spaces/{space_id}/documents/{document_id}/workspace")
    def document_workspace(space_id: str, document_id: str, request: Request) -> dict[str, Any]:
        subject = principal(request)
        require_document_manager(runtime, subject, document_id)
        require_local_tenant(runtime, subject)
        page = queries.documents(space_id, document_id=document_id, limit=1)
        if not page.items:
            raise ResourceNotFoundError(document_id)
        item = page.items[0]
        document = runtime.repository.get_document(document_id)
        versions = runtime.repository.get_versions(document_id)
        try:
            quality = runtime.repository.get_quality_report(item["version_id"])
        except ResourceNotFoundError:
            quality = None
        review = runtime.repository.get_latest_review(item["version_id"])
        runtime.lifecycle_store.reload()
        record = runtime.lifecycle_store.documents.get(document_id)
        with db.connection() as connection:
            publication_row = db.one(
                connection,
                "SELECT payload_json FROM publication_actions WHERE tenant_id=? AND version_id=? "
                "ORDER BY updated_at DESC,action_key DESC LIMIT 1",
                (subject.tenant_id, item["version_id"]),
            )
        publication = json.loads(publication_row["payload_json"]) if publication_row else {}
        return {
            **item,
            "row_version": document["row_version"],
            "versions": versions,
            "quality": quality,
            "review": review,
            "publication": {
                key: publication[key]
                for key in ("phase", "error_code", "retryable")
                if key in publication
            },
            "lifecycle": {
                **lifecycle_response(record).model_dump(),
                "version_history": record.version_history,
            }
            if record
            else None,
        }

    @router.get("/api/document-versions/{version_id}/original/preview")
    def original(version_id: str, request: Request) -> FileResponse:
        subject = principal(request)
        version = runtime.repository.get_version(version_id)
        document_id = str(version["document_id"])
        ensure_document_previewable(runtime, document_id, subject)
        path = runtime.storage._safe_path("original", str(version["original_key"]))
        if not path.is_file():
            raise ResourceNotFoundError(version_id)
        return FileResponse(
            path,
            filename=Path(path).name,
            media_type="application/octet-stream",
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
        )

    @router.post("/api/document-versions/{version_id}:review-and-publish")
    @version_condition(runtime)
    def review_publish(
        version_id: str,
        body: PublicationConfirmation,
        request: Request,
        action_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=191),
    ) -> dict[str, Any]:
        subject = principal(request)
        require_local_tenant(runtime, subject)
        require_role(subject, "knowledge_maintainer", "admin")
        version = runtime.repository.get_version(version_id)
        document_id = str(version["document_id"])
        ensure_document_previewable(runtime, document_id, subject)
        digest = hashlib.sha256(
            json.dumps({"comment": body.comment, "user": subject.user_id}, sort_keys=True).encode()
        ).hexdigest()
        # Existing lifecycle commands are single-instance serialized and durably idempotent.
        with runtime.lifecycle_store.lock:
            with db.connection() as connection:
                saved = db.one(
                    connection,
                    "SELECT payload_json FROM publication_actions WHERE tenant_id=? AND "
                    "version_id=? AND action_key=?",
                    (subject.tenant_id, version_id, action_key),
                )
            state = (
                json.loads(saved["payload_json"])
                if saved
                else {"phase": "pending", "request_hash": digest}
            )
            if state["request_hash"] != digest:
                raise IdempotencyConflictError("PUBLICATION_CONFIRMATION_CHANGED")
            if state["phase"] == "published":
                return state

            def save() -> None:
                with db.transaction() as connection:
                    db.execute(
                        connection,
                        "DELETE FROM publication_actions WHERE tenant_id=? AND version_id=?"
                        " AND action_key=?",
                        (subject.tenant_id, version_id, action_key),
                    )
                    db.execute(
                        connection,
                        "INSERT INTO "
                        "publication_actions(tenant_id,version_id,action_key,payload_json,updated_at)"
                        " VALUES (?,?,?,?,?)",
                        (subject.tenant_id, version_id, action_key, json.dumps(state), time.time()),
                    )

            command_key = hashlib.sha256(
                f"{subject.tenant_id}:{version_id}:{action_key}".encode()
            ).hexdigest()
            if state["phase"] == "pending":
                quality = runtime.repository.get_quality_report(version_id)
                if (
                    version["processing_state"] != "VALIDATED"
                    or quality["disposition"] == "BLOCKED_REAL_VALIDATION"
                ):
                    raise HTTPException(409, "PUBLICATION_QUALITY_NOT_READY")
                existing = runtime.repository.get_latest_review(version_id)
                policy = existing.get("security_projection") if existing else None
                if policy is None and existing and existing.get("security_projection_json"):
                    policy = json.loads(existing["security_projection_json"])
                if policy is None:
                    runtime.lifecycle_store.reload()
                    active = runtime.lifecycle_store.documents.get(document_id)
                    current_id = active.active_version_id if active else None
                    current_id = current_id or runtime.repository.get_document(document_id).get(
                        "current_version_id"
                    )
                    current_review = (
                        runtime.repository.get_latest_review(current_id) if current_id else None
                    )
                    policy = current_review.get("security_projection") if current_review else None
                    if (
                        policy is None
                        and current_review
                        and current_review.get("security_projection_json")
                    ):
                        policy = json.loads(current_review["security_projection_json"])
                security = SecurityProjectionRequest(
                    **{
                        k: v
                        for k, v in (
                            policy
                            or {
                                "visibility": "TENANT",
                                "classification_level": 0,
                                "acl_scope_tokens": [],
                            }
                        ).items()
                        if k in SecurityProjectionRequest.model_fields
                    }
                )
                review = apply_document_review(
                    runtime,
                    version_id,
                    DocumentReviewRequest(
                        decision="APPROVED", comment=body.comment, security_projection=security
                    ),
                    request,
                    "workspace-review-" + command_key,
                )
                state.update(phase="reviewed", review_id=review.review_id)
                save()
            try:
                runtime.lifecycle_store.reload()
                record = runtime.lifecycle_service.publish(
                    document_id,
                    version_id,
                    event_id="workspace-publish-" + command_key,
                    trace_id=request.state.request_id,
                )
                set_current = getattr(runtime.repository, "set_document_current_version", None)
                if callable(set_current) and not getattr(
                    runtime.lifecycle_store, "durable_publication_intents", False
                ):
                    set_current(document_id, version_id)
                state.update(
                    phase="published",
                    lifecycle=lifecycle_response(record).model_dump(),
                    error_code=None,
                )
            except Exception as error:
                # Provider exceptions may contain endpoints: persist a safe diagnostic class.
                state.update(error_code=type(error).__name__, retryable=True)
            save()
            return state

    return router
