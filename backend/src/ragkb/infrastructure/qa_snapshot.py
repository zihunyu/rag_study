"""Content, publication, security and effective-time identity of the whole KB."""

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from ragkb.adapters.mysql_retrieval import MySQLRetrievalControlPlane
from ragkb.config import EnvSettings
from ragkb.contracts.ports import RetrievalReleasePort
from ragkb.infrastructure.visual_assets import VisualAssetStore


def configuration_revision(settings: EnvSettings) -> str:
    # Only the final digest is retained. Secret/endpoint changes also invalidate
    # receipts, without storing credentials or URLs in cache keys or reports.
    values = {
        key: value.get_secret_value() if hasattr(value, "get_secret_value") else value
        for key, value in settings.model_dump().items()
    }
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode())
    for path in sorted(Path(__file__).parents[1].rglob("*.py")):
        digest.update(path.relative_to(Path(__file__).parents[1]).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class MySQLQASnapshot:
    def __init__(
        self,
        control: MySQLRetrievalControlPlane,
        release: RetrievalReleasePort,
        visual_store: VisualAssetStore,
        default_space: str,
    ) -> None:
        self.control, self.release, self.visual_store, self.default_space = (
            control,
            release,
            visual_store,
            default_space,
        )

    def __call__(self, tenant: str, space: str | None) -> str | None:
        space = space or self.default_space
        before = self.release.current_release(tenant, space)
        # No LIMIT or GROUP_CONCAT: either hash every projection, or do not reuse.
        rows = self.control._fetch_all(
            "SELECT chunk_id, document_id, document_version_id, content_checksum, "
            "SHA2(display_text,256) AS display_hash, SHA2(retrieval_text,256) AS retrieval_hash, "
            "SHA2(CAST(locator_json AS CHAR),256) AS locator_hash, parent_chunk_id, visibility, "
            "acl_scope_tokens_json, classification_level, lifecycle_projection, valid_from_epoch, "
            "valid_to_epoch, permission_revision, current_version FROM retrieval_chunk_projections "
            "WHERE tenant_id=%s AND space_id=%s AND index_generation_id=%s ORDER BY chunk_id",
            (tenant, space, before.active_generation_id),
        )
        now = int(time.time())
        material = [
            dict(
                row,
                effective=int(row["valid_from_epoch"]) <= now
                and (int(row["valid_to_epoch"]) == 0 or now < int(row["valid_to_epoch"])),
            )
            for row in rows
        ]
        visuals = {
            version: self.visual_store.list_assets(version)
            for version in sorted({str(row["document_version_id"]) for row in rows})
        }
        for version, assets in visuals.items():
            for asset in assets:
                if asset.get("status") == "verified":
                    # Integrity check includes actual bytes before an early hit;
                    # metadata alone cannot detect a missing/corrupt image file.
                    try:
                        self.visual_store.read_image(version, asset)
                    except (ValueError, OSError):
                        return None
        after = self.release.current_release(tenant, space)
        if before != after:
            return None
        return hashlib.sha256(
            json.dumps(
                {"release": asdict(after), "corpus": material, "visuals": visuals},
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
