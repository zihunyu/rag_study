"""Evidence-derived production acceptance gate; configuration alone can never pass it."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RealAcceptanceEvidence:
    provider: str
    embedding_revision: str
    reranker_revision: str
    model_revision: str
    prompt_revision: str
    index_generation_id: str
    dataset_revision: str
    metrics: Mapping[str, float]
    evaluated_at: str
    evidence_hash: str

    @property
    def verified(self) -> bool:
        body = {
            "provider": self.provider,
            "embedding_revision": self.embedding_revision,
            "reranker_revision": self.reranker_revision,
            "model_revision": self.model_revision,
            "prompt_revision": self.prompt_revision,
            "index_generation_id": self.index_generation_id,
            "dataset_revision": self.dataset_revision,
            "metrics": dict(self.metrics),
            "evaluated_at": self.evaluated_at,
        }
        actual = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return bool(
            self.provider
            and self.index_generation_id
            and self.dataset_revision
            and self.metrics
            and self.evidence_hash == actual
        )


def load_acceptance_evidence(path: Path) -> RealAcceptanceEvidence:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("ACCEPTANCE_EVIDENCE_NOT_OBJECT")
    required = {
        "provider",
        "embedding_revision",
        "reranker_revision",
        "model_revision",
        "prompt_revision",
        "index_generation_id",
        "dataset_revision",
        "metrics",
        "evaluated_at",
        "evidence_hash",
    }
    if set(loaded) != required or not isinstance(loaded["metrics"], dict):
        raise ValueError("ACCEPTANCE_EVIDENCE_SCHEMA_INVALID")
    evidence = RealAcceptanceEvidence(
        **{
            **loaded,
            "metrics": {str(key): float(value) for key, value in loaded["metrics"].items()},
        }
    )
    if not evidence.verified:
        raise ValueError("ACCEPTANCE_EVIDENCE_HASH_INVALID")
    return evidence
