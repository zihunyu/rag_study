"""OpenAI-compatible Embedding and Reranker adapters with billable-call guard."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

import httpx

from ragkb.config import EnvSettings


class BillableCallApprovalRequired(RuntimeError):
    pass


class JsonTransport(Protocol):
    real_network: bool

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]: ...


class HttpxJsonTransport:
    real_network = True

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        response = httpx.post(url, headers=dict(headers), json=dict(payload), timeout=timeout)
        response.raise_for_status()
        loaded = response.json()
        if not isinstance(loaded, Mapping):
            raise ValueError("model response must be a JSON object")
        return loaded


class _GuardedModelAdapter:
    def __init__(
        self,
        *,
        transport: JsonTransport | None,
        external_call_approved: bool,
    ) -> None:
        self._transport = transport or HttpxJsonTransport()
        self._external_call_approved = external_call_approved

    def _guard(self) -> None:
        if self._transport.real_network and not self._external_call_approved:
            raise BillableCallApprovalRequired("BILLABLE_MODEL_CALL_APPROVAL_REQUIRED")


class OpenAICompatibleEmbeddingAdapter(_GuardedModelAdapter):
    revision = "openai-compatible-embedding:g2-v1"

    def __init__(
        self,
        settings: EnvSettings,
        *,
        transport: JsonTransport | None = None,
        external_call_approved: bool = False,
    ) -> None:
        super().__init__(transport=transport, external_call_approved=external_call_approved)
        self._settings = settings
        self.dimension = settings.embedding_dimension

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self._guard()
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("embedding input must contain non-empty text")
        key = self._settings.embedding_api_key
        response = self._transport.post_json(
            f"{self._settings.embedding_base_url.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {key.get_secret_value() if key else ''}"},
            payload={"model": self._settings.embedding_model, "input": list(texts)},
            timeout=self._settings.llm_timeout_seconds,
        )
        data = response.get("data")
        if not isinstance(data, Sequence) or len(data) != len(texts):
            raise ValueError("embedding response count mismatch")
        vectors: list[list[float]] = []
        for item in data:
            if not isinstance(item, Mapping) or not isinstance(item.get("embedding"), Sequence):
                raise ValueError("embedding response item is invalid")
            vector = [float(value) for value in item["embedding"]]
            if len(vector) != self.dimension or not all(math.isfinite(value) for value in vector):
                raise ValueError("embedding dimension or values are invalid")
            vectors.append(vector)
        return vectors

    def probe_plan(self) -> dict[str, object]:
        return {
            "adapter": self.revision,
            "approval_required": "BILLABLE_MODEL_CALL_APPROVAL_REQUIRED",
            "request_count": 1,
            "input_count": 1,
            "input_policy": "fixed synthetic public text only; no repository or user content",
            "success_checks": [
                "HTTP success",
                "response item count equals input count",
                f"vector dimension equals {self.dimension}",
                "all vector values finite",
                "latency and rate-limit headers recorded without payload",
            ],
            "endpoint_configured": bool(self._settings.embedding_base_url),
            "model_configured": bool(self._settings.embedding_model),
            "external_call_approved": self._external_call_approved,
            "real_call_performed": False,
            "api_key_in_output": False,
        }


class OpenAICompatibleRerankerAdapter(_GuardedModelAdapter):
    revision = "openai-compatible-reranker:g2-v1"

    def __init__(
        self,
        settings: EnvSettings,
        *,
        transport: JsonTransport | None = None,
        external_call_approved: bool = False,
    ) -> None:
        super().__init__(transport=transport, external_call_approved=external_call_approved)
        self._settings = settings

    def rerank(self, query: str, documents: Sequence[str]) -> Sequence[int]:
        self._guard()
        if not documents:
            return []
        if not query.strip() or any(not document.strip() for document in documents):
            raise ValueError("reranker query and documents must be non-empty")
        key = self._settings.reranker_api_key
        response = self._transport.post_json(
            f"{self._settings.reranker_base_url.rstrip('/')}/rerank",
            headers={"Authorization": f"Bearer {key.get_secret_value() if key else ''}"},
            payload={
                "model": self._settings.reranker_model,
                "query": query,
                "documents": list(documents),
                "top_n": min(len(documents), self._settings.reranker_max_candidates),
            },
            timeout=self._settings.reranker_timeout_seconds,
        )
        results = response.get("results")
        if not isinstance(results, Sequence):
            raise ValueError("reranker response results are invalid")
        order: list[int] = []
        for item in results:
            if not isinstance(item, Mapping):
                raise ValueError("reranker result item is invalid")
            index = int(item.get("index", -1))
            if index < 0 or index >= len(documents) or index in order:
                raise ValueError("reranker returned an invalid index")
            order.append(index)
        return order

    def probe_plan(self) -> dict[str, object]:
        return {
            "adapter": self.revision,
            "approval_required": "BILLABLE_MODEL_CALL_APPROVAL_REQUIRED",
            "request_count": 1,
            "document_count": 2,
            "input_policy": "fixed synthetic public query/documents only; no repository content",
            "success_checks": [
                "HTTP success",
                "returned indexes are unique and in range",
                "known relevant synthetic document ranks first",
                "latency and rate-limit headers recorded without payload",
            ],
            "endpoint_configured": bool(self._settings.reranker_base_url),
            "model_configured": bool(self._settings.reranker_model),
            "external_call_approved": self._external_call_approved,
            "real_call_performed": False,
            "api_key_in_output": False,
        }
