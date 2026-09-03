"""Signed, threshold-checked production acceptance evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import SecretStr


@dataclass(frozen=True)
class RealAcceptanceEvidence:
    provider: str
    embedding_revision: str
    reranker_revision: str
    model_revision: str
    verifier_revision: str
    tokenizer_revision: str
    prompt_revision: str
    index_generation_id: str
    dataset_revision: str
    case_count: int
    query_types: tuple[str, ...]
    metrics: Mapping[str, float]
    thresholds: Mapping[str, float]
    passed: bool
    evaluated_at_epoch: int
    quality_report_sha256: str
    source_commit: str
    ci_run_id: str
    performance_scope: tuple[int, ...]
    budget_report_sha256: str
    payload_sha256: str
    signature: str

    def body(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "embedding_revision": self.embedding_revision,
            "reranker_revision": self.reranker_revision,
            "model_revision": self.model_revision,
            "verifier_revision": self.verifier_revision,
            "tokenizer_revision": self.tokenizer_revision,
            "prompt_revision": self.prompt_revision,
            "index_generation_id": self.index_generation_id,
            "dataset_revision": self.dataset_revision,
            "case_count": self.case_count,
            "query_types": list(self.query_types),
            "metrics": dict(self.metrics),
            "thresholds": dict(self.thresholds),
            "passed": self.passed,
            "evaluated_at_epoch": self.evaluated_at_epoch,
            "quality_report_sha256": self.quality_report_sha256,
            "source_commit": self.source_commit,
            "ci_run_id": self.ci_run_id,
            "performance_scope": list(self.performance_scope),
            "budget_report_sha256": self.budget_report_sha256,
        }

    def canonical_payload(self) -> bytes:
        return json.dumps(self.body(), sort_keys=True, separators=(",", ":")).encode("utf-8")

    def verified(
        self,
        signing_key: SecretStr,
        *,
        max_age_hours: int,
        min_cases: int = 1,
        required_query_types: tuple[str, ...] = (),
        now_epoch: int | None = None,
    ) -> bool:
        payload = self.canonical_payload()
        actual_hash = hashlib.sha256(payload).hexdigest()
        actual_signature = hmac.new(
            signing_key.get_secret_value().encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        now = int(time.time()) if now_epoch is None else now_epoch
        metrics_pass = bool(self.thresholds) and all(
            key in self.metrics and float(self.metrics[key]) >= float(minimum)
            for key, minimum in self.thresholds.items()
        )
        return bool(
            self.provider
            and self.index_generation_id
            and self.dataset_revision
            and self.passed
            and self.case_count >= min_cases
            and set(required_query_types).issubset(self.query_types)
            and metrics_pass
            and 0 <= now - self.evaluated_at_epoch <= max_age_hours * 3600
            and len(self.quality_report_sha256) == 64
            and len(self.source_commit) == 40
            and self.ci_run_id
            and self.performance_scope == (1, 5, 20)
            and len(self.budget_report_sha256) == 64
            and hmac.compare_digest(self.payload_sha256, actual_hash)
            and hmac.compare_digest(self.signature, actual_signature)
        )


def load_acceptance_evidence(
    path: Path,
    *,
    signing_key: SecretStr,
    max_age_hours: int,
    min_cases: int = 1,
    required_query_types: tuple[str, ...] = (),
    now_epoch: int | None = None,
) -> RealAcceptanceEvidence:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("ACCEPTANCE_EVIDENCE_NOT_OBJECT")
    required = {
        "provider",
        "embedding_revision",
        "reranker_revision",
        "model_revision",
        "verifier_revision",
        "tokenizer_revision",
        "prompt_revision",
        "index_generation_id",
        "dataset_revision",
        "case_count",
        "query_types",
        "metrics",
        "thresholds",
        "passed",
        "evaluated_at_epoch",
        "quality_report_sha256",
        "source_commit",
        "ci_run_id",
        "performance_scope",
        "budget_report_sha256",
        "payload_sha256",
        "signature",
    }
    if (
        set(loaded) != required
        or not isinstance(loaded["metrics"], dict)
        or not isinstance(loaded["thresholds"], dict)
        or not isinstance(loaded["query_types"], list)
        or not isinstance(loaded["performance_scope"], list)
    ):
        raise ValueError("ACCEPTANCE_EVIDENCE_SCHEMA_INVALID")
    evidence = RealAcceptanceEvidence(
        **{
            **loaded,
            "metrics": {str(key): float(value) for key, value in loaded["metrics"].items()},
            "thresholds": {str(key): float(value) for key, value in loaded["thresholds"].items()},
            "query_types": tuple(map(str, loaded["query_types"])),
            "performance_scope": tuple(map(int, loaded["performance_scope"])),
        }
    )
    if not evidence.verified(
        signing_key,
        max_age_hours=max_age_hours,
        min_cases=min_cases,
        required_query_types=required_query_types,
        now_epoch=now_epoch,
    ):
        raise ValueError("ACCEPTANCE_EVIDENCE_SIGNATURE_OR_THRESHOLD_INVALID")
    return evidence
