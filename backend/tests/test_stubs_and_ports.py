from __future__ import annotations

from ragkb.adapters.stubs import (
    DeterministicEmbedding,
    DeterministicGeneration,
    DeterministicReranker,
    InMemoryJobQueue,
    StubPermissionProjection,
)


def test_deterministic_model_adapters() -> None:
    embedding = DeterministicEmbedding()
    assert embedding.embed(["same"]) == embedding.embed(["same"])
    assert len(embedding.embed(["same"])[0]) == embedding.dimension

    reranker = DeterministicReranker()
    assert list(reranker.rerank("exact term", ["noise", "exact term"])) == [1, 0]

    generation = DeterministicGeneration()
    assert generation.generate("q", []) == "insufficient_evidence"
    assert generation.generate("q", ["E1"]).startswith("stub_answer:")


def test_permission_and_queue_fakes() -> None:
    permission = StubPermissionProjection()
    assert permission.allowed(["group:a"], ["group:a"])
    assert not permission.allowed(["group:a"], ["group:b"])
    assert permission.watermark_ready(10, 10)
    assert not permission.watermark_ready(11, 10)

    queue = InMemoryJobQueue()
    assert queue.dequeue() is None
    queue.enqueue("job-1", {"attempt": 1})
    assert queue.dequeue() == ("job-1", {"attempt": 1})
