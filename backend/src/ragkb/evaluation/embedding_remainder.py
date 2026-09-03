"""Build the exact 459-chunk format-remainder Embedding snapshot."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ragkb.application.provider_runners import EmbeddingChunk
from ragkb.config import load_env
from ragkb.infrastructure.provider_results import LocalProviderResultStore

EXPECTED_INPUT_CHECKPOINT_HASHES = {
    "mineru-scan-attempt-v4.json": (
        "182e4a4811d4708074a4c39fd522d5cf011e8955bef489bd22021a98fa402b07"
    ),
    "mineru-scan-attempt-v5.json": (
        "71200ca9a76c9655e043886f6e5e996223584e534cadbfca99e3c883fa2678e7"
    ),
    "mineru-docx-pdf-attempt-v1.json": (
        "fb545b7d3dd973e1a112adcffb71d6f4027484b83916418621d8c880ad469da8"
    ),
    "embedding-attempt-v2.json": (
        "f26bd8d126ca56457803fc1f0062ccb5b45395b9e936cb08d506f609bc9153a8"
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _completed_evidence(path: Path) -> list[dict[str, object]]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    namespace = loaded.get("mineru", {}) if isinstance(loaded, dict) else {}
    values = namespace if isinstance(namespace, dict) else {}
    return [
        dict(value["evidence"])
        for key, value in values.items()
        if key != "_manifest"
        and isinstance(value, dict)
        and value.get("state") == "COMPLETED"
        and isinstance(value.get("evidence"), dict)
    ]


def load_format_remainder_chunks(root: Path) -> tuple[list[EmbeddingChunk], dict[str, object]]:
    checkpoint_root = root / "artifacts/final-validation/provider-checkpoints"
    actual_hashes = {
        name: _sha256(checkpoint_root / name) for name in EXPECTED_INPUT_CHECKPOINT_HASHES
    }
    if actual_hashes != EXPECTED_INPUT_CHECKPOINT_HASHES:
        raise ValueError("EMBEDDING_V3_INPUT_CHECKPOINT_MISMATCH")
    loaded = load_env(root)
    if loaded.settings is None:
        raise ValueError("CONFIG_INVALID")
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (root / artifacts_root).resolve()
    store = LocalProviderResultStore(artifacts_root)
    groups = {
        "scan_v4": _completed_evidence(checkpoint_root / "mineru-scan-attempt-v4.json"),
        "scan_v5": _completed_evidence(checkpoint_root / "mineru-scan-attempt-v5.json"),
        "docx_pdf": _completed_evidence(checkpoint_root / "mineru-docx-pdf-attempt-v1.json"),
    }
    expected_group_chunks = {"scan_v4": 75, "scan_v5": 82, "docx_pdf": 302}
    chunks: list[EmbeddingChunk] = []
    group_counts: dict[str, int] = {}
    artifact_hashes: set[str] = set()
    for group_name, evidence_records in groups.items():
        before = len(chunks)
        for evidence in evidence_records:
            artifact_id = evidence.get("artifact_id")
            result_hash = evidence.get("result_hash")
            if not isinstance(artifact_id, str) or not isinstance(result_hash, str):
                raise ValueError("EMBEDDING_V3_ARTIFACT_EVIDENCE_INVALID")
            artifact_hashes.add(result_hash)
            nodes = store.read_mineru_nodes(artifact_id)
            for node in nodes:
                node_id = node.get("node_id")
                text = node.get("display_text")
                if not isinstance(node_id, str) or not node_id:
                    raise ValueError("EMBEDDING_V3_NODE_ID_INVALID")
                if not isinstance(text, str) or not text.strip():
                    continue
                chunks.append(EmbeddingChunk(node_id, text))
        group_counts[group_name] = len(chunks) - before
    if group_counts != expected_group_chunks or len(chunks) != 459:
        raise ValueError("EMBEDDING_V3_CHUNK_COUNT_MISMATCH")
    if len({chunk.chunk_id for chunk in chunks}) != 459:
        raise ValueError("EMBEDDING_V3_CHUNK_ID_DUPLICATE")
    v2_loaded = json.loads(
        (checkpoint_root / "embedding-attempt-v2.json").read_text(encoding="utf-8")
    )
    v2_namespace = v2_loaded.get("embedding", {})
    v2_completed = [
        value
        for key, value in v2_namespace.items()
        if key != "_manifest" and isinstance(value, dict) and value.get("state") == "COMPLETED"
    ]
    v2_chunks = sum(len(value.get("chunk_ids", [])) for value in v2_completed)
    if v2_chunks != 669 or len(v2_completed) != 67:
        raise ValueError("EMBEDDING_V2_COMPLETION_MISMATCH")
    return chunks, {
        "revision": "embedding-v3-format-remainder-input:v1",
        "chunk_count": 459,
        "group_chunk_counts": group_counts,
        "artifact_hash_count": len(artifact_hashes),
        "chunk_ids_unique": True,
        "nonempty_display_text_only": True,
        "embedding_v2_completed_chunks": 669,
        "embedding_v2_completed_batches": 67,
        "checkpoint_hashes_match": True,
        "content_output": False,
        "source_names_output": False,
    }
