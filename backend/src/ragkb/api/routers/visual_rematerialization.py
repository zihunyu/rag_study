"""Reviewed-only, optimistic and idempotent new-version materialization endpoints."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ragkb.api.support import principal, require_role
from ragkb.infrastructure.visual_assets import VisualAssetStore
from ragkb.infrastructure.visual_rematerialization import (
    REVISION,
    build_snapshot,
    digest,
    rematerialize_canonical,
)
from ragkb.runtime_components import RuntimeComponents


class RematerializationRequest(BaseModel):
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    backup_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def build_rematerialization_router(
    runtime: RuntimeComponents,
    store: VisualAssetStore,
    management_version: Callable[[str, Request], dict[str, Any]],
) -> APIRouter:
    router = APIRouter(tags=["visual-rematerialization"])

    def snapshot(version_id: str, request: Request) -> dict[str, Any]:
        require_role(principal(request), "knowledge_maintainer", "admin")
        version = management_version(version_id, request)
        if not runtime.settings.ocr_enabled:
            raise HTTPException(409, "OCR_CONFIGURATION_REQUIRED")
        try:
            return build_snapshot(runtime, version)
        except (ValueError, OSError) as error:
            code = (
                str(error)
                if str(error).startswith("VISUAL_REMATERIALIZATION_")
                else "VISUAL_REMATERIALIZATION_SOURCE_UNAVAILABLE"
            )
            raise HTTPException(409, code) from error

    @router.get("/api/document-versions/{version_id}/visual-rematerialization")
    def diagnose(version_id: str, request: Request) -> dict[str, Any]:
        with runtime.lifecycle_store.lock:
            backup = snapshot(version_id, request)
        preview = rematerialize_canonical(backup, "dry-run")
        return {
            "source_version_id": version_id,
            "document_id": backup["document"]["id"],
            "row_version": backup["document"]["row_version"],
            "generator_revision": REVISION,
            "input_fingerprint": digest(backup),
            "creates_draft_only": True,
            "vision_model_calls": 0,
            "embedding_reindex_required": True,
            "backup": backup,
            "preview": [
                {"locator": node.locator.to_dict(), "text": node.display_text}
                for node in preview.nodes
                if node.metadata.get("visual_asset_ids")
            ],
            "original_download_url": f"/api/document-versions/{version_id}/original/preview",
        }

    @router.post("/api/document-versions/{version_id}:rematerialize-visuals", status_code=202)
    def submit(
        version_id: str,
        body: RematerializationRequest,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        key: str = Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ) -> dict[str, Any]:
        require_role(principal(request), "knowledge_maintainer", "admin")
        with runtime.lifecycle_store.lock:
            management_version(version_id, request)
            operation = "visual-rematerialization:" + version_id
            request_hash = runtime.uploads.request_hash(
                {"body": body.model_dump(), "condition": if_match}
            )
            replay = runtime.repository.idempotency_response(operation, key, request_hash)
            if replay is not None:
                return replay
            backup = snapshot(version_id, request)
            if digest(backup) != body.input_fingerprint:
                raise HTTPException(409, "VISUAL_REMATERIALIZATION_SNAPSHOT_CHANGED")
            try:
                condition = int(if_match.strip('"'))
            except ValueError as error:
                raise HTTPException(422, "DOCUMENT_CONDITION_INVALID") from error
            if condition != int(backup["document"]["row_version"]):
                raise HTTPException(409, "VISUAL_REMATERIALIZATION_DOCUMENT_CHANGED")
            source = runtime.storage.path_for("original", str(backup["version"]["original_key"]))
            data = source.read_bytes()
            document_id = str(backup["document"]["id"])
            session_key = (
                "visual-materialize:"
                + hashlib.sha256((version_id + ":" + key).encode()).hexdigest()
            )
            session = runtime.uploads.create_version_session(
                document_id=document_id,
                expected_document_row_version=condition,
                space_id=runtime.repository.get_document_space(document_id),
                filename=source.name,
                expected_size=len(data),
                expected_sha256=hashlib.sha256(data).hexdigest(),
                declared_mime="application/octet-stream",
                idempotency_key=session_key,
            )
            inherited = backup["version_plan"]
            plan = {
                key: inherited[key]
                for key in (
                    "exclude_assets",
                    "exclude_sections",
                    "exclude_objects",
                    "confirmed_independent",
                )
                if key in inherited
            }
            plan.update(
                {
                    "base_version_id": version_id,
                    "rematerialization": {
                        "snapshot": backup,
                        "fingerprint": body.input_fingerprint,
                        "backup_receipt_sha256": body.backup_receipt_sha256,
                        "generator_revision": REVISION,
                        "requested_by": principal(request).user_id,
                    },
                }
            )
            store.ledger.put("session_plan", session.id, plan, immutable=True)
            if session.state.value in {"CREATED", "FAILED"}:
                session = runtime.uploads.upload_content(
                    session.id, data, expected_row_version=session.row_version
                )
            result = runtime.uploads.complete(
                session.id,
                expected_row_version=session.row_version,
                idempotency_key=session_key,
            )
            result = {
                **result,
                "materialization_revision": REVISION,
                "source_version_id": version_id,
                "input_fingerprint": body.input_fingerprint,
                "creates_draft_only": True,
            }
            runtime.repository.save_idempotency_response(
                operation, key, request_hash, str(result["document_version_id"]), result
            )
            return result

    return router
