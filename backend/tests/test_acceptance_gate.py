from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from ragkb.application.acceptance import load_acceptance_evidence


def test_acceptance_is_derived_from_hashed_runtime_evidence(tmp_path: Path) -> None:
    body = {
        "provider": "approved-provider",
        "embedding_revision": "embed-v1",
        "reranker_revision": "rerank-v1",
        "model_revision": "llm-v1",
        "prompt_revision": "prompt-v1",
        "index_generation_id": "generation-v1",
        "dataset_revision": "gold-v1",
        "metrics": {"recall_at_k": 0.95},
        "evaluated_at": "2026-09-03T00:00:00Z",
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps({**body, "evidence_hash": digest}), encoding="utf-8")

    assert load_acceptance_evidence(path).verified is True
    path.write_text(json.dumps({**body, "evidence_hash": "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="HASH_INVALID"):
        load_acceptance_evidence(path)
