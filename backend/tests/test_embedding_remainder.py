from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from ragkb.application.provider_runners import EmbeddingExecutionRunner
from ragkb.contracts.provider_execution import ProviderExecutionError
from ragkb.evaluation.embedding_remainder import load_format_remainder_chunks
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore

from scripts.run_embedding_format_remainder import build_plan


class _Transport:
    real_network = False

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def embed(self, texts, idempotency_key, timeout_seconds):
        del timeout_seconds
        self.calls.append(idempotency_key)
        if self.fail:
            raise ProviderExecutionError(
                "EMBEDDING_FAKE_DETERMINISTIC_FAILURE", outcome_unknown=False
            )
        return [[0.1, 0.2, 0.3] for _ in texts]


def test_format_remainder_snapshot_is_exact_unique_and_plan_is_zero_call() -> None:
    root = Path(__file__).resolve().parents[2]
    chunks, evidence = load_format_remainder_chunks(root)
    assert len(chunks) == 459
    assert len({chunk.chunk_id for chunk in chunks}) == 459
    assert all(chunk.text.strip() for chunk in chunks)
    assert evidence["group_chunk_counts"] == {
        "scan_v4": 75,
        "scan_v5": 82,
        "docx_pdf": 302,
    }
    assert evidence["content_output"] is False
    plan = build_plan()
    assert plan["max_chunks"] == 459
    assert plan["batch_size"] == 10
    assert plan["max_batches"] == 46
    assert plan["approved_by_user"] is True
    assert plan["runner_review_required_before_execution"] is False
    assert plan["executed"] is True
    assert plan["execution_status"] == "COMPLETED"
    assert plan["completed_batches"] == 46
    assert plan["vector_count"] == 459
    assert plan["zilliz_write_approved"] is False
    assert (
        root / "artifacts/final-validation/provider-checkpoints/"
        "embedding-format-remainder-attempt-v3.json"
    ).exists()


def test_format_remainder_fake_success_is_46_batches_and_isolated(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    chunks, _ = load_format_remainder_chunks(root)
    old = root / "artifacts/final-validation/provider-checkpoints/embedding-attempt-v2.json"
    old_hash = hashlib.sha256(old.read_bytes()).hexdigest()
    checkpoint = JsonCheckpointStore(tmp_path / "embedding-v3.json")
    transport = _Transport()
    result = EmbeddingExecutionRunner(
        transport,
        checkpoint,
        dimension=3,
        external_call_approved=False,
        batch_size=10,
        max_chunks=459,
        max_batches=46,
    ).run(chunks)
    assert result["chunk_count"] == 459
    assert result["batch_count"] == result["completed_batches"] == 46
    assert len(transport.calls) == 46
    assert result["automatic_retries"] == 0
    assert result["zilliz_write_performed"] is False
    persisted = checkpoint.path.read_text(encoding="utf-8")
    assert hashlib.sha256(old.read_bytes()).hexdigest() == old_hash
    loaded = json.loads(persisted)["embedding"]
    assert all("text" not in value for value in loaded.values() if isinstance(value, dict))
    records = [value for key, value in loaded.items() if key != "_manifest"]
    assert sum(len(value["chunk_ids"]) for value in records) == 459


def test_format_remainder_failure_stops_without_retry(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    chunks, _ = load_format_remainder_chunks(root)
    checkpoint = JsonCheckpointStore(tmp_path / "failed-v3.json")
    transport = _Transport(fail=True)
    runner = EmbeddingExecutionRunner(
        transport,
        checkpoint,
        dimension=3,
        external_call_approved=False,
        batch_size=10,
        max_chunks=459,
        max_batches=46,
    )
    with pytest.raises(ProviderExecutionError, match="FAKE_DETERMINISTIC_FAILURE"):
        runner.run(chunks)
    assert len(transport.calls) == 1
    with pytest.raises(ProviderExecutionError, match="FAKE_DETERMINISTIC_FAILURE"):
        runner.run(chunks)
    assert len(transport.calls) == 1
