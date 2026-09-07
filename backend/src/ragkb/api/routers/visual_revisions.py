"""Human correction and retry create an immutable version before the existing publish gate."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ragkb.api.support import principal, require_role
from ragkb.domain.visual_comparison import extraction_diff
from ragkb.domain.visuals import VisualExtraction, reviewed_extraction
from ragkb.infrastructure.visual_assets import VisualAssetStore
from ragkb.infrastructure.visual_revisions import exclusion_closure
from ragkb.runtime_components import RuntimeComponents


class VisualEdit(BaseModel):
    asset_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    extraction: VisualExtraction


class VisualRevisionRequest(BaseModel):
    edits: list[VisualEdit] = Field(default_factory=list, max_length=100)
    retry_assets: list[str] = Field(default_factory=list, max_length=100)
    exclude_assets: list[str] = Field(default_factory=list, max_length=100)
    reason: str = Field(min_length=1, max_length=2000, pattern=r".*\S.*")
    confirmed_against_original: bool = False
    confirm_independent_sections: bool = False


def build_visual_revision_router(
    runtime: RuntimeComponents,
    store: VisualAssetStore,
    management_version: Callable[[str, Request], dict[str, Any]],
) -> APIRouter:
    router = APIRouter(tags=["visual-revisions"])

    @router.get("/api/document-versions/{version_id}/visual-exclusion-preview")
    def preview(version_id: str, asset_id: str, request: Request) -> dict[str, Any]:
        management_version(version_id, request)
        assets = {a["id"]: a for a in store.list_assets(version_id)}
        if asset_id not in assets:
            raise HTTPException(422, "VISUAL_ASSET_SELECTION_INVALID")
        sections = exclusion_closure(
            runtime.repository, version_id, assets, [assets[asset_id].get("section_path", "root")]
        )
        return {
            "sections": sections,
            "asset_ids": [
                a["id"]
                for a in assets.values()
                if any(
                    a.get("section_path", "root") == s
                    or a.get("section_path", "root").startswith(s + " / ")
                    for s in sections
                )
            ],
        }

    @router.get("/api/document-versions/{version_id}/visual-usage")
    def usage(version_id: str, request: Request) -> dict[str, Any]:
        management_version(version_id, request)
        return store.ledger.usage_report(version_id)

    @router.get("/api/document-versions/{version_id}/visual-diff")
    def diff(version_id: str, request: Request, base_version_id: str) -> dict[str, Any]:
        version = management_version(version_id, request)
        base = management_version(base_version_id, request)
        if base["document_id"] != version["document_id"]:
            raise HTTPException(422, "VISUAL_DIFF_DOCUMENT_MISMATCH")
        old = {a["id"]: a for a in store.list_assets(base_version_id)}
        return {
            "items": [
                {
                    "asset_id": a["id"],
                    "changes": [
                        {
                            "path": "/status",
                            "label": "图片可用范围",
                            "before": "包含原识别内容",
                            "after": "排除此图及关联章节",
                        }
                    ]
                    if a["status"] == "excluded"
                    and old.get(a["id"], {}).get("status") != "excluded"
                    else extraction_diff(
                        old.get(a["id"], {}).get("extraction") or {}, a.get("extraction") or {}
                    ),
                    "before_status": old.get(a["id"], {}).get("status"),
                    "after_status": a["status"],
                }
                for a in store.list_assets(version_id)
            ]
        }

    @router.post("/api/document-versions/{version_id}:revise-visuals", status_code=202)
    def revise(
        version_id: str,
        body: VisualRevisionRequest,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        key: str = Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ) -> dict[str, Any]:
        require_role(principal(request), "knowledge_maintainer", "admin")
        with runtime.lifecycle_store.lock:
            version = management_version(version_id, request)
            assets = {a["id"]: a for a in store.list_assets(version_id)}
            if not runtime.settings.ocr_enabled:
                raise HTTPException(409, "OCR_CONFIGURATION_REQUIRED")
            operation = "visual-revision:" + version_id
            command = {"body": body.model_dump(mode="json"), "condition": if_match}
            fingerprint = runtime.uploads.request_hash(command)
            replay = runtime.repository.idempotency_response(operation, key, fingerprint)
            if replay:
                return replay
            ids = (
                set(body.retry_assets) | set(body.exclude_assets) | {e.asset_id for e in body.edits}
            )
            if not ids or not ids.issubset(assets):
                raise HTTPException(422, "VISUAL_ASSET_SELECTION_INVALID")
            if any(
                a.get("stage") in {"queued", "extracting", "verifying", "checking_text"}
                for a in assets.values()
            ):
                raise HTTPException(409, "VISUAL_PROCESSING_BUSY")
            if (set(body.retry_assets) & set(body.exclude_assets)) or any(
                e.asset_id in body.exclude_assets for e in body.edits
            ):
                raise HTTPException(422, "VISUAL_REVISION_ACTION_CONFLICT")
            if body.edits and not body.confirmed_against_original:
                raise HTTPException(422, "VISUAL_HUMAN_REVIEW_REQUIRED")
            if any(edit.extraction.issues() for edit in body.edits):
                raise HTTPException(422, "VISUAL_UNRESOLVED_UNCERTAINTIES")
            for edit in body.edits:
                original = assets[edit.asset_id].get("extraction")
                if original:
                    edit.extraction = reviewed_extraction(
                        VisualExtraction.model_validate(original), edit.extraction
                    )
            if body.exclude_assets and not body.confirm_independent_sections:
                raise HTTPException(422, "VISUAL_PARTIAL_DEPENDENCY_REVIEW_REQUIRED")
            inherited = store.ledger.get("version_plan", version_id)
            sections = sorted(
                set(inherited.get("exclude_sections", []))
                | {assets[i].get("section_path", "root") for i in body.exclude_assets}
            )
            sections = exclusion_closure(runtime.repository, version_id, assets, sections)
            history: list[dict[str, Any]] = [
                {
                    "actor": principal(request).user_id,
                    "reason": body.reason,
                    "asset_id": e.asset_id,
                    "changes": extraction_diff(
                        assets[e.asset_id].get("extraction") or {}, e.extraction.model_dump()
                    ),
                }
                for e in body.edits
            ]
            plan = {
                "base_version_id": version_id,
                "edits": {e.asset_id: e.extraction.model_dump() for e in body.edits},
                "retry_assets": body.retry_assets,
                "exclude_assets": sorted(
                    set(body.exclude_assets) | set(inherited.get("exclude_assets", []))
                ),
                "exclude_sections": sections,
                "history": history,
                "reason": body.reason,
                "actor": principal(request).user_id,
                "confirmed_independent": body.confirm_independent_sections,
            }
            try:
                condition = int(if_match.strip('"'))
            except ValueError as error:
                raise HTTPException(422, "DOCUMENT_CONDITION_INVALID") from error
            source = runtime.storage.path_for("original", str(version["original_key"]))
            data = source.read_bytes()
            document_id = str(version["document_id"])
            session = runtime.uploads.create_version_session(
                document_id=document_id,
                expected_document_row_version=condition,
                space_id=runtime.repository.get_document_space(document_id),
                filename=source.name,
                expected_size=len(data),
                expected_sha256=hashlib.sha256(data).hexdigest(),
                declared_mime="application/octet-stream",
                idempotency_key="visual-revision:" + key,
            )
            for entry in history:
                entry["at"] = session.created_at or 0
            store.ledger.put("session_plan", session.id, plan, immutable=True)
            if session.state.value in {"CREATED", "FAILED"}:
                session = runtime.uploads.upload_content(
                    session.id, data, expected_row_version=session.row_version
                )
            result = runtime.uploads.complete(
                session.id,
                expected_row_version=session.row_version,
                idempotency_key="visual-revision:" + key,
            )
            runtime.repository.save_idempotency_response(
                operation, key, fingerprint, str(result["document_version_id"]), result
            )
            return result

    return router
