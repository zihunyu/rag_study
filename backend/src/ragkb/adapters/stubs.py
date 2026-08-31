"""Deterministic local fakes; their output is never real acceptance evidence."""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Mapping, Sequence


class DeterministicEmbedding:
    revision = "deterministic-fake-embedding:g0-v1"
    dimension = 8

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8"), usedforsecurity=False).digest()
            values = [((digest[index] / 255.0) * 2.0) - 1.0 for index in range(self.dimension)]
            magnitude = sum(value * value for value in values) ** 0.5 or 1.0
            vectors.append([value / magnitude for value in values])
        return vectors


class DeterministicReranker:
    revision = "deterministic-fake-reranker:g0-v1"

    def rerank(self, query: str, documents: Sequence[str]) -> Sequence[int]:
        query_terms = set(query.casefold().split())
        scored = []
        for index, document in enumerate(documents):
            overlap = len(query_terms.intersection(document.casefold().split()))
            scored.append((-overlap, index))
        return [index for _, index in sorted(scored)]


class DeterministicGeneration:
    revision = "deterministic-fake-generation:g0-v1"

    def generate(self, question: str, evidence: Sequence[str]) -> str:
        if not evidence:
            return "insufficient_evidence"
        digest = hashlib.sha256(
            (question + "\n" + "\n".join(evidence)).encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        return f"stub_answer:{digest[:16]}"


class StubPermissionProjection:
    def allowed(self, resource_tokens: Sequence[str], subject_tokens: Sequence[str]) -> bool:
        return bool(set(resource_tokens).intersection(subject_tokens))

    def watermark_ready(self, active_watermark: int, observed_watermark: int) -> bool:
        return observed_watermark >= active_watermark


class InMemoryJobQueue:
    def __init__(self) -> None:
        self._items: deque[tuple[str, Mapping[str, object]]] = deque()

    def enqueue(self, job_id: str, payload: Mapping[str, object]) -> None:
        self._items.append((job_id, dict(payload)))

    def dequeue(self) -> tuple[str, Mapping[str, object]] | None:
        return self._items.popleft() if self._items else None
