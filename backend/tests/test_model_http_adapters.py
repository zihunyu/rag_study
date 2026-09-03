from __future__ import annotations

from pathlib import Path

import pytest
from ragkb.adapters.model_http import (
    BillableCallApprovalRequired,
    OpenAICompatibleEmbeddingAdapter,
    OpenAICompatibleRerankerAdapter,
)
from ragkb.config import load_env


class _MockTransport:
    real_network = False

    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post_json(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response


class _NetworkTransport(_MockTransport):
    real_network = True


def _settings(tmp_path: Path):
    secret = "model-secret-value"  # noqa: S105
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            (
                "EMBEDDING_BASE_URL=https://embedding.example/v1",
                f"EMBEDDING_API_KEY={secret}",
                "EMBEDDING_MODEL=embed-test",
                "EMBEDDING_DIMENSION=3",
                "ZILLIZ_CLOUD_DIMENSION=3",
                "RERANKER_BASE_URL=https://reranker.example/v1",
                f"RERANKER_API_KEY={secret}",
                "RERANKER_MODEL=rerank-test",
            )
        ),
        encoding="utf-8",
    )
    loaded = load_env(Path(__file__).resolve().parents[2], env_path=env, environ={})
    assert loaded.settings is not None
    return loaded.settings, secret


def test_embedding_and_reranker_mock_contracts_do_not_make_real_calls(tmp_path: Path) -> None:
    settings, secret = _settings(tmp_path)
    embedding_transport = _MockTransport(
        {"data": [{"embedding": [1.0, 0.0, 0.0]}, {"embedding": [0.0, 1.0, 0.0]}]}
    )
    reranker_transport = _MockTransport({"results": [{"index": 1}, {"index": 0}]})
    embedding = OpenAICompatibleEmbeddingAdapter(settings, transport=embedding_transport)
    reranker = OpenAICompatibleRerankerAdapter(settings, transport=reranker_transport)

    assert embedding.embed(["a", "b"]) == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert reranker.rerank("q", ["a", "b"]) == [1, 0]
    assert embedding.probe_plan()["real_call_performed"] is False
    assert reranker.probe_plan()["real_call_performed"] is False
    assert secret not in str(embedding.probe_plan())
    assert secret not in str(reranker.probe_plan())


def test_real_network_model_calls_are_blocked_without_explicit_approval(tmp_path: Path) -> None:
    settings, _ = _settings(tmp_path)
    transport = _NetworkTransport({"data": [{"embedding": [1.0, 0.0, 0.0]}]})
    adapter = OpenAICompatibleEmbeddingAdapter(settings, transport=transport)

    with pytest.raises(BillableCallApprovalRequired):
        adapter.embed(["probe"])

    assert transport.calls == []
    assert adapter.probe_plan()["external_call_approved"] is False
