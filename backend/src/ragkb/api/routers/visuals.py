"""Original/recognized image comparison, version-safe reprocessing and source delivery."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response
from PIL import Image, UnidentifiedImageError

from ragkb.api.support import ensure_document_previewable, principal
from ragkb.domain.uploads import ResourceNotFoundError
from ragkb.infrastructure.visual_assets import VisualAssetStore
from ragkb.runtime_components import RuntimeComponents


def image_response(
    store: VisualAssetStore, version_id: str, asset_id: str, *, normalized: bool = False
) -> Response:
    try:
        data = store.read_image(version_id, store.get(version_id, asset_id))
        with Image.open(io.BytesIO(data)) as image:
            mime = Image.MIME.get(image.format or "", "")
        if normalized:
            from ragkb.document_processing.image_views import normalize_image

            with normalize_image(data, max_bytes=100_000_000, max_pixels=100_000_000) as bitmap:
                output = io.BytesIO()
                bitmap.save(output, format="PNG")
                data, mime = output.getvalue(), "image/png"
        if mime not in {
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/gif",
            "image/bmp",
            "image/tiff",
        }:
            raise ValueError("VISUAL_FORMAT_UNSUPPORTED")
    except (ValueError, OSError, UnidentifiedImageError) as error:
        raise HTTPException(404, "VISUAL_SOURCE_UNAVAILABLE") from error
    return Response(
        data,
        media_type=mime,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


def build_visual_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter(tags=["visual-evidence"])
    store = VisualAssetStore(runtime.storage)

    def management_version(version_id: str, request: Request) -> dict[str, Any]:
        version = runtime.repository.get_version(version_id)
        ensure_document_previewable(runtime, str(version["document_id"]), principal(request))
        return version

    @router.get("/api/document-versions/{version_id}/visuals")
    def list_visuals(version_id: str, request: Request) -> dict[str, Any]:
        management_version(version_id, request)
        assets = store.list_assets(version_id)
        return {
            "items": [store.public(a, version_id) for a in assets],
            "verified_count": sum(a.get("status") == "verified" for a in assets),
            "review_count": sum(a.get("status") not in {"verified", "excluded"} for a in assets),
            "excluded_count": sum(a.get("status") == "excluded" for a in assets),
            "processing": store.ledger.get("version", version_id),
            "usage": {
                key: value
                for key, value in store.ledger.usage_report(version_id).items()
                if key != "records"
            },
        }

    @router.get("/api/document-versions/{version_id}/visuals/{asset_id}/image")
    def original_image(
        version_id: str, asset_id: str, request: Request, normalized: bool = False
    ) -> Response:
        management_version(version_id, request)
        return image_response(store, version_id, asset_id, normalized=normalized)

    @router.post("/api/document-versions/{version_id}:reparse-visuals", status_code=202)
    def reparse(
        version_id: str,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        key: str = Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ) -> dict[str, Any]:
        with runtime.lifecycle_store.lock:
            version = management_version(version_id, request)
            if not runtime.settings.ocr_enabled:
                raise HTTPException(409, "OCR_CONFIGURATION_REQUIRED")
            operation = "visual-reparse:" + version_id
            command_hash = runtime.uploads.request_hash(
                {"version_id": version_id, "condition": if_match}
            )
            replay = runtime.repository.idempotency_response(operation, key, command_hash)
            if replay is not None:
                return replay
            try:
                condition = int(if_match.strip('"'))
            except ValueError as error:
                raise HTTPException(422, "DOCUMENT_CONDITION_INVALID") from error
            document_id = str(version["document_id"])
            source = runtime.storage.path_for("original", str(version["original_key"]))
            if not source.is_file():
                raise ResourceNotFoundError(version_id)
            data = source.read_bytes()
            session = runtime.uploads.create_version_session(
                document_id=document_id,
                expected_document_row_version=condition,
                space_id=runtime.repository.get_document_space(document_id),
                filename=Path(source).name,
                expected_size=len(data),
                expected_sha256=hashlib.sha256(data).hexdigest(),
                declared_mime="application/octet-stream",
                idempotency_key="visual:" + key,
            )
            if session.state.value in {"CREATED", "FAILED"}:
                session = runtime.uploads.upload_content(
                    session.id, data, expected_row_version=session.row_version
                )
            result = runtime.uploads.complete(
                session.id,
                expected_row_version=session.row_version,
                idempotency_key="visual:" + key,
            )
            runtime.repository.save_idempotency_response(
                operation, key, command_hash, str(result["document_version_id"]), result
            )
            return result

    from ragkb.api.routers.visual_revisions import build_visual_revision_router

    router.include_router(build_visual_revision_router(runtime, store, management_version))
    return router
