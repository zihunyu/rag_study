from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from pydantic import SecretStr
from ragkb.application.acceptance import load_acceptance_evidence


def test_acceptance_requires_signature_freshness_and_passing_thresholds(tmp_path: Path) -> None:
    key = SecretStr("acceptance-signing-key-for-tests")
    body = {
        "provider": "approved-provider",
        "embedding_revision": "embed-v1",
        "reranker_revision": "rerank-v1",
        "model_revision": "llm-v1",
        "prompt_revision": "prompt-v1",
        "index_generation_id": "generation-v1",
        "dataset_revision": "gold-v1",
        "case_count": 100,
        "query_types": ["semantic", "permission"],
        "metrics": {"recall_at_k": 0.95},
        "thresholds": {"recall_at_k": 0.9},
        "passed": True,
        "evaluated_at_epoch": int(time.time()),
        "quality_report_sha256": "a" * 64,
        "source_commit": "b" * 40,
        "ci_run_id": "ci-1",
    }
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(payload).hexdigest()
    signature = hmac.new(key.get_secret_value().encode(), payload, hashlib.sha256).hexdigest()
    path = tmp_path / "acceptance.json"
    path.write_text(
        json.dumps({**body, "payload_sha256": digest, "signature": signature}),
        encoding="utf-8",
    )

    evidence = load_acceptance_evidence(path, signing_key=key, max_age_hours=1)
    assert evidence.verified(key, max_age_hours=1)
    path.write_text(
        json.dumps({**body, "payload_sha256": digest, "signature": "0" * 64}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="SIGNATURE_OR_THRESHOLD_INVALID"):
        load_acceptance_evidence(path, signing_key=key, max_age_hours=1)
