"""Atomic anonymous local persistence for validated provider results."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload, usedforsecurity=False).hexdigest()


class LocalProviderResultStore:
    revision = "local-provider-result-store:v1"

    def __init__(self, artifacts_root: Path) -> None:
        self.root = (artifacts_root / "provider-results" / "mineru").resolve()

    @staticmethod
    def _artifact_id(anonymous_id: str, result_hash: str) -> str:
        if (
            not anonymous_id
            or len(result_hash) != 64
            or any(character not in "0123456789abcdef" for character in result_hash.casefold())
        ):
            raise ValueError("PROVIDER_RESULT_IDENTITY_INVALID")
        return hashlib.sha256(
            f"{anonymous_id}:{result_hash.casefold()}".encode(), usedforsecurity=False
        ).hexdigest()[:32]

    def _target(self, artifact_id: str) -> Path:
        if len(artifact_id) != 32 or any(
            character not in "0123456789abcdef" for character in artifact_id.casefold()
        ):
            raise ValueError("PROVIDER_ARTIFACT_ID_INVALID")
        target = (self.root / artifact_id.casefold()).resolve()
        if target.parent != self.root:
            raise ValueError("PROVIDER_ARTIFACT_PATH_INVALID")
        return target

    @staticmethod
    def _write_durable(path: Path, payload: bytes) -> None:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o600)

    def _metadata(self, artifact_id: str, target: Path) -> dict[str, object]:
        manifest_path = target / "manifest.json"
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or loaded.get("artifact_id") != artifact_id:
            raise ValueError("PROVIDER_ARTIFACT_MANIFEST_INVALID")
        zip_path = target / "provider-result.zip"
        nodes_path = target / "normalized-nodes.json"
        zip_payload = zip_path.read_bytes()
        nodes_payload = nodes_path.read_bytes()
        if (
            _sha256(zip_payload) != loaded.get("zip_sha256")
            or len(zip_payload) != loaded.get("zip_bytes")
            or _sha256(nodes_payload) != loaded.get("nodes_sha256")
        ):
            raise ValueError("PROVIDER_ARTIFACT_INTEGRITY_INVALID")
        return {
            "artifact_id": artifact_id,
            "artifact_ref": f"provider-results/mineru/{artifact_id}",
            "zip_sha256": loaded["zip_sha256"],
            "zip_bytes": loaded["zip_bytes"],
            "nodes_sha256": loaded["nodes_sha256"],
            "node_count": loaded["node_count"],
            "chunk_count": loaded["chunk_count"],
        }

    def persist_mineru_result(
        self,
        anonymous_id: str,
        result_hash: str,
        zip_payload: bytes,
        nodes: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, object]:
        if _sha256(zip_payload) != result_hash.casefold():
            raise ValueError("PROVIDER_RESULT_HASH_MISMATCH")
        artifact_id = self._artifact_id(anonymous_id, result_hash)
        target = self._target(artifact_id)
        if target.is_dir():
            return self._metadata(artifact_id, target)
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{artifact_id}-", dir=self.root)).resolve()
        try:
            nodes_document = {
                "revision": "mineru-normalized-nodes:v1",
                "artifact_id": artifact_id,
                "anonymous_sample_id": anonymous_id,
                "result_hash": result_hash.casefold(),
                "nodes": [dict(node) for node in nodes],
            }
            nodes_payload = (
                json.dumps(
                    nodes_document,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode()
            chunk_count = sum(bool(str(node.get("display_text", "")).strip()) for node in nodes)
            manifest = {
                "revision": self.revision,
                "artifact_id": artifact_id,
                "zip_sha256": result_hash.casefold(),
                "zip_bytes": len(zip_payload),
                "nodes_sha256": _sha256(nodes_payload),
                "node_count": len(nodes),
                "chunk_count": chunk_count,
            }
            manifest_payload = (
                json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n"
            ).encode()
            self._write_durable(temporary / "provider-result.zip", zip_payload)
            self._write_durable(temporary / "normalized-nodes.json", nodes_payload)
            self._write_durable(temporary / "manifest.json", manifest_payload)
            try:
                os.replace(temporary, target)
            except OSError:
                if not target.is_dir():
                    raise
            return self._metadata(artifact_id, target)
        finally:
            if temporary.is_dir():
                shutil.rmtree(temporary)

    def read_mineru_nodes(self, artifact_id: str) -> Sequence[Mapping[str, Any]]:
        target = self._target(artifact_id)
        self._metadata(artifact_id, target)
        loaded = json.loads((target / "normalized-nodes.json").read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or loaded.get("artifact_id") != artifact_id:
            raise ValueError("PROVIDER_NORMALIZED_NODES_INVALID")
        nodes = loaded.get("nodes")
        if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
            raise ValueError("PROVIDER_NORMALIZED_NODES_INVALID")
        return nodes
