"""Version-scoped visual assets; original bytes and audit receipts are never public paths."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.infrastructure.visual_ledger import VisualLedger


class VisualAssetStore:
    def __init__(self, storage: LocalFileStorage) -> None:
        self.storage = storage
        self.ledger = VisualLedger(storage.path_for("artifacts", "visual-ledger.sqlite"))

    @staticmethod
    def prefix(version_id: str) -> str:
        return "visual/" + hashlib.sha256(version_id.encode()).hexdigest()

    def manifest_key(self, version_id: str) -> str:
        return self.prefix(version_id) + "/manifest.json"

    def list_assets(self, version_id: str) -> list[dict[str, Any]]:
        assets = self.ledger.assets(version_id)
        if assets:
            return assets
        path = self.storage.path_for("artifacts", self.manifest_key(version_id))
        if not path.is_file():
            return []
        content = json.loads(path.read_text(encoding="utf-8"))
        if content.get("version_id") != version_id:
            raise ValueError("VISUAL_VERSION_MISMATCH")
        return list(content["assets"])

    def write_manifest(self, version_id: str, assets: list[dict[str, Any]]) -> None:
        for ordinal, asset in enumerate(assets):
            self.ledger.upsert_asset(version_id, {**asset, "ordinal": ordinal})
        self.storage.write_bytes(
            "artifacts",
            self.manifest_key(version_id),
            json.dumps(
                {"version_id": version_id, "assets": assets},
                ensure_ascii=False,
                sort_keys=True,
            ).encode(),
        )

    def get(self, version_id: str, asset_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{32}", asset_id):
            raise ValueError("VISUAL_ASSET_ID_INVALID")
        for asset in self.list_assets(version_id):
            if asset["id"] == asset_id:
                return asset
        raise FileNotFoundError("VISUAL_ASSET_NOT_FOUND")

    def read_image(self, version_id: str, asset: dict[str, Any]) -> bytes:
        key = asset["storage_key"]
        if not key.startswith(self.prefix(version_id) + "/"):
            raise ValueError("VISUAL_ASSET_PATH_INVALID")
        data = self.storage.path_for("artifacts", key).read_bytes()
        if hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise ValueError("VISUAL_ASSET_INTEGRITY_INVALID")
        return data

    def save_image(self, version_id: str, data: bytes, locator: dict[str, Any]) -> dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        asset_id = hashlib.sha256(
            (digest + json.dumps(locator, sort_keys=True)).encode()
        ).hexdigest()[:32]
        key = self.prefix(version_id) + "/" + asset_id + ".image"
        self.storage.write_bytes("artifacts", key, data)
        return {
            "id": asset_id,
            "storage_key": key,
            "sha256": digest,
            "locator": locator,
            "size_bytes": len(data),
        }

    @staticmethod
    def public(asset: dict[str, Any], version_id: str) -> dict[str, Any]:
        from ragkb.domain.graph_facts import accepted_graph_projection
        from ragkb.domain.visual_graph import to_mermaid
        from ragkb.domain.visuals import VisualExtraction

        result = {
            k: asset.get(k)
            for k in (
                "id",
                "locator",
                "size_bytes",
                "status",
                "issues",
                "revision",
                "width",
                "height",
                "section_path",
                "caption",
                "stage",
                "ordinal",
                "regions",
                "local_check",
                "origin",
                "source_asset_id",
                "history",
                "cache_hit",
                "updated_at",
                "region_exclusions",
                "coordinate_space",
                "source_version_id",
            )
        }
        extraction = asset.get("extraction")
        if extraction:
            parsed = VisualExtraction.model_validate(extraction)
            graphs = (
                [accepted_graph_projection(g) for g in parsed.graphs]
                if asset.get("status") == "verified"
                else parsed.graphs
            )
            result.update(
                extraction=parsed.model_dump(),
                mermaid=[to_mermaid(g) for g in graphs if g.nodes],
                table_html=[t.as_html() for t in parsed.tables],
                graph_coverage=[
                    {
                        "graph": index,
                        "partial": source != projected,
                        "retained_nodes": len(projected.nodes),
                        "retained_edges": len(projected.edges),
                        "excluded_nodes": len(source.nodes) - len(projected.nodes),
                        "excluded_edges": len(source.edges) - len(projected.edges),
                    }
                    for index, (source, projected) in enumerate(
                        zip(parsed.graphs, graphs, strict=True)
                    )
                ],
            )
        result["image_url"] = f"/api/document-versions/{version_id}/visuals/{asset['id']}/image"
        return result
