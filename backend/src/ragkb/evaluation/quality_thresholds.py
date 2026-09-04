"""Version-controlled, non-zero RAG quality acceptance thresholds."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

QUALITY_METRICS = frozenset(
    {
        "recall_at_k",
        "precision_at_k",
        "hit_rate",
        "mrr",
        "ndcg_at_k",
        "answer_token_f1",
        "citation_precision",
        "citation_recall",
        "no_answer_accuracy",
    }
)


@dataclass(frozen=True)
class QualityThresholdPolicy:
    values: Mapping[str, float]
    sha256: str

    @property
    def revision(self) -> str:
        return f"rag-quality-thresholds:{self.sha256[:16]}"


def load_quality_threshold_policy(path: Path) -> QualityThresholdPolicy:
    raw = path.read_bytes()
    loaded: Any = json.loads(raw)
    if not isinstance(loaded, dict) or set(loaded) != QUALITY_METRICS:
        raise ValueError("RAG_QUALITY_THRESHOLD_SCHEMA_INVALID")
    values = {str(name): float(value) for name, value in loaded.items()}
    if any(not 0 < value <= 1 for value in values.values()):
        raise ValueError("RAG_QUALITY_THRESHOLDS_MUST_BE_NON_ZERO")
    return QualityThresholdPolicy(values, hashlib.sha256(raw).hexdigest())
