from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from ragkb.adapters.auth import LocalSingleUserAuthenticator
from ragkb.adapters.model_http import (
    OpenAICompatibleBufferedGenerator,
    OpenAICompatibleClaimVerifier,
    OpenAICompatibleEmbeddingAdapter,
    OpenAICompatibleRerankerAdapter,
)
from ragkb.adapters.zilliz import ZillizCloudAdapter
from ragkb.config import load_env
from ragkb.runtime_components import build_runtime_components
from ragkb.runtime_profiles.production import ProductionRuntimeFactory


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
                f"APP_REVISION={'c' * 40}",
                "RAG_RUNTIME_PROFILE=production",
                "REAL_PROVIDER_CALLS_ENABLED=true",
                "RAG_ACCEPTANCE_SIGNING_KEY=production-acceptance-test-key",
                "EMBEDDING_INPUT_COST_PER_MILLION_CNY=1",
                "RERANKER_INPUT_COST_PER_MILLION_CNY=1",
                "LLM_INPUT_COST_PER_MILLION_CNY=1",
                "LLM_OUTPUT_COST_PER_MILLION_CNY=1",
                "VERIFIER_INPUT_COST_PER_MILLION_CNY=1",
                "VERIFIER_OUTPUT_COST_PER_MILLION_CNY=1",
                "EXTERNAL_LIFECYCLE_MUTATIONS_ENABLED=true",
                "RETRIEVAL_ACTIVE_GENERATION_ID=production-generation-1",
                "LLM_BASE_URL=https://llm.example/v1",
                "LLM_API_KEY=test-llm-key",
                "LLM_MODEL=test-llm",
                "LLM_ALLOW_HTTP=false",
                "VERIFIER_BASE_URL=https://verifier.example/v1",
                "VERIFIER_API_KEY=test-verifier-key",
                "VERIFIER_MODEL=test-independent-verifier",
                "EMBEDDING_BASE_URL=https://embedding.example/v1",
                "EMBEDDING_API_KEY=test-embedding-key",
                "EMBEDDING_MODEL=test-embedding",
                "RERANKER_BASE_URL=https://reranker.example/v1",
                "RERANKER_API_KEY=test-reranker-key",
                "RERANKER_MODEL=test-reranker",
                "ZILLIZ_CLOUD_URI=https://cluster.example.zilliz.com.cn:19530",
                "ZILLIZ_CLOUD_TOKEN=test-zilliz-token",
                "MINERU_TOKENS=test-mineru-token",
                "MYSQL_PASSWORD=test-mysql-password",
                "MYSQL_HOST=127.0.0.1",
                "MYSQL_USER=rag_app",
                "REDIS_HOST=127.0.0.1",
                "ZILLIZ_CLOUD_DIMENSION=1024",
                "EMBEDDING_DIMENSION=1024",
                "AI_APPROVED_PROCESSING_REGIONS=cn",
                "APP_SECRET_KEY=test-app-secret-key-long",
                'REFERENCE_SIGNING_KEYRING={"v1":"test-reference-key-long-enough"}',
                "REFERENCE_ACTIVE_KID=v1",
                "AUTH_MODE=oidc",
                "OIDC_ISSUER_URL=https://id.example",
                "OIDC_AUDIENCE=test-audience",
                "OIDC_CLIENT_ID=test-client",
                "OIDC_CLIENT_SECRET=test-client-secret",
                "OIDC_TENANT_ID=tenant-production",
                "OIDC_DEFAULT_SPACE_ID=space-production",
                "TOKENIZER_ARTIFACT_PATH="
                f"{root / 'backend/tests/fixtures/tokenizer/minimal-tokenizer.json'}",
                "TOKENIZER_ARTIFACT_SHA256=e05d0c453e652ff400d7782318a7e21ec3535dc584e314f5112bd169b6c1177e",
                "TOKENIZER_ID=test-wordlevel-v1",
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
    assert isinstance(components.qa_service.verifier, OpenAICompatibleClaimVerifier)
    assert components.search_service.real_acceptance is False
    assert "deterministic" not in components.search_service.embedding.revision
    assert components.model_transport is not None
    for transport in components.provider_transports:
        transport.close()


def test_production_factory_allows_loopback_local_auth_for_full_provider_testing(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    loaded = load_env(root, environ={})
    assert loaded.settings is not None
    settings = loaded.settings.model_copy(
        update={
            "app_env": "production",
            "rag_runtime_profile": "production",
            "app_host": "127.0.0.1",
            "auth_mode": "local_single_user",
            "auth_local_tenant": "local",
        }
    )
    factory = ProductionRuntimeFactory()

    authenticator = factory.build_authenticator(settings, "local")

    assert isinstance(authenticator, LocalSingleUserAuthenticator)
    assert authenticator.authenticate(None).tenant_id == "local"


def test_production_factory_rejects_network_exposure_with_local_auth() -> None:
    root = Path(__file__).resolve().parents[2]
    loaded = load_env(root, environ={})
    assert loaded.settings is not None
    settings = loaded.settings.model_copy(
        update={
            "app_env": "production",
            "rag_runtime_profile": "production",
            "app_host": "0.0.0.0",  # noqa: S104 - intentional negative security test.
            "auth_mode": "local_single_user",
        }
    )

    with pytest.raises(RuntimeError, match="LOCAL_SINGLE_USER_AUTH_REQUIRES_LOOPBACK"):
        ProductionRuntimeFactory().build_authenticator(settings, "local")
