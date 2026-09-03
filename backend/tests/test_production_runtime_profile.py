from __future__ import annotations

import shutil
from pathlib import Path

from ragkb.adapters.model_http import (
    OpenAICompatibleBufferedGenerator,
    OpenAICompatibleEmbeddingAdapter,
    OpenAICompatibleRerankerAdapter,
)
from ragkb.adapters.zilliz import ZillizCloudAdapter
from ragkb.runtime_components import build_runtime_components


def test_production_profile_contains_no_deterministic_rag_components(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    config = tmp_path / "config"
    config.mkdir()
    shutil.copyfile(root / "config/.env.example", config / ".env.example")
    (config / ".env").write_text(
        "\n".join(
            (
                "APP_ENV=production",
                "APP_DEBUG=false",
                "RAG_RUNTIME_PROFILE=production",
                "REAL_PROVIDER_CALLS_ENABLED=true",
                "RETRIEVAL_ACTIVE_GENERATION_ID=production-generation-1",
                "LLM_BASE_URL=https://llm.example/v1",
                "LLM_ALLOW_HTTP=false",
            )
        ),
        encoding="utf-8",
    )

    components = build_runtime_components(
        repository_root=tmp_path,
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )

    assert isinstance(components.search_service.embedding, OpenAICompatibleEmbeddingAdapter)
    assert isinstance(components.search_service.index, ZillizCloudAdapter)
    assert isinstance(components.search_service.reranker, OpenAICompatibleRerankerAdapter)
    assert isinstance(components.qa_service.generator, OpenAICompatibleBufferedGenerator)
    assert components.search_service.real_acceptance is False
    assert "deterministic" not in components.search_service.embedding.revision
    assert components.model_transport is not None
    components.model_transport.close()
