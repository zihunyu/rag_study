from pathlib import Path

import pytest
from ragkb.adapters.model_http import OpenAICompatibleQuestionAssessor
from ragkb.application.tracing import InMemoryTracer
from ragkb.config import load_env
from ragkb.infrastructure.sqlite import SQLiteDatabase
from ragkb.runtime_profiles.factory import select_runtime_factory
from ragkb.runtime_profiles.local import LocalRuntimeFactory
from ragkb.runtime_profiles.production import ProductionRuntimeFactory


def test_profile_selection_is_explicit_and_factories_reject_profile_drift(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    settings = load_env(root).settings
    assert settings is not None
    local_settings = settings.model_copy(
        update={"app_env": "testing", "rag_runtime_profile": "local"}
    )
    production_settings = settings.model_copy(
        update={"app_env": "production", "rag_runtime_profile": "production"}
    )
    database = SQLiteDatabase(tmp_path / "profile.sqlite3")

    assert isinstance(select_runtime_factory(local_settings), LocalRuntimeFactory)
    assert isinstance(select_runtime_factory(production_settings), ProductionRuntimeFactory)
    with pytest.raises(RuntimeError, match="LOCAL_FACTORY_REJECTS_PRODUCTION_SETTINGS"):
        LocalRuntimeFactory().build_persistence(production_settings, database)
    with pytest.raises(RuntimeError, match="PRODUCTION_FACTORY_REQUIRES_PRODUCTION_SETTINGS"):
        ProductionRuntimeFactory().build_persistence(local_settings, database)


def test_production_factory_wires_question_assessor_without_provider_calls(tmp_path):
    from test_model_http_adapters import _settings

    settings, _ = _settings(tmp_path)
    settings = settings.model_copy(
        update={
            "app_env": "production",
            "rag_runtime_profile": "production",
            "real_provider_calls_enabled": False,
            "vector_backend": "zilliz",
        }
    )
    factory = ProductionRuntimeFactory()
    database = SQLiteDatabase(tmp_path / "runtime.sqlite3")
    persistence = factory.build_persistence(settings, database)
    retrieval = factory.build_retrieval(settings, database, persistence, InMemoryTracer())
    try:
        assert isinstance(retrieval.question_assessor, OpenAICompatibleQuestionAssessor)
        assert retrieval.question_assessor.revision.startswith(
            "openai-compatible-question-assessor:"
        )
    finally:
        for transport in retrieval.provider_transports:
            transport.close()
