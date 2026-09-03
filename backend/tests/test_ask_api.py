from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from ragkb.adapters.rag_stubs import (
    DeterministicBufferedGenerator,
    StaticFinalPermission,
    SyntheticEvidenceProvider,
)
from ragkb.api.app import create_app
from ragkb.application.qa import TrustedQAService
from ragkb.domain.lifecycle import LifecycleState
from ragkb.domain.rag import Evidence
from ragkb.runtime_components import build_runtime_components


def _components(tmp_path: Path):
    return build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )


def _answered_components(tmp_path: Path):
    components = _components(tmp_path)
    components.lifecycle_service.register_document(
        "hidden-document", "hidden-version", trace_id="setup"
    )
    record = components.lifecycle_store.documents["hidden-document"]
    record.lifecycle_state = LifecycleState.ACTIVE
    record.visible = True
    components.lifecycle_store.persist_state(tenant_id=components.tenant_id)
    evidence = Evidence(
        evidence_id="E1",
        chunk_id="hidden-chunk",
        document_id="hidden-document",
        document_version_id="hidden-version",
        text="设备保修期为三年。",
        locator={"page": 2},
        valid_from_epoch=0,
        valid_to_epoch=0,
        authority_rank=10,
        permission_revision=1,
        authorized=True,
        current_version=True,
    )
    service = TrustedQAService(
        SyntheticEvidenceProvider((evidence,)),
        DeterministicBufferedGenerator(),
        StaticFinalPermission(),
        components.reference_signer,
        components.rag_repository,
    )
    return replace(components, qa_service=service)


def test_ask_source_preview_and_feedback_contract(tmp_path: Path) -> None:
    components = _answered_components(tmp_path)
    client = TestClient(create_app(components))

    response = client.post("/api/v1/ask", json={"question": "保修期多久？"})

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "answered"
    assert result["verified"] is True
    assert result["real_acceptance"] is False
    source_url = result["citations"][0]["source_url"]
    assert "hidden-document" not in source_url
    source = client.get(source_url)
    assert source.status_code == 200
    assert source.json() == {
        "evidence_id": "E1",
        "text": "设备保修期为三年。",
        "locator": {"page": 2},
    }
    assert "hidden-document" not in source.text
    feedback = client.post(
        f"/api/v1/rag-runs/{result['rag_run_id']}/feedback",
        json={"rating": 5, "reason_code": "helpful", "comment": "ok"},
    )
    assert feedback.status_code == 200
    assert feedback.json()["accepted"] is True
    assert feedback.json()["index_generation_id"]


def test_sse_never_emits_answer_before_verified_progress(tmp_path: Path) -> None:
    client = TestClient(create_app(_answered_components(tmp_path)))

    response = client.post("/api/v1/ask:stream", json={"question": "保修期多久？"})

    assert response.status_code == 200
    text = response.text
    assert text.count("event: progress") == 3
    assert text.count("event: result") == 1
    assert text.index('"stage": "verified"') < text.index("设备保修期为三年")
    assert "event: token" not in text


def test_default_runtime_refuses_to_answer_without_evidence(tmp_path: Path) -> None:
    client = TestClient(create_app(_components(tmp_path)))

    response = client.post("/api/v1/ask", json={"question": "unknown"})

    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_evidence"
    assert response.json()["answer"] is None


def test_tampered_source_and_unknown_feedback_do_not_leak_resource_existence(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(_answered_components(tmp_path)))
    answered = client.post("/api/v1/ask", json={"question": "q"}).json()
    source_url = answered["citations"][0]["source_url"]
    tampered_parts = source_url.split("/")
    tampered_parts[4] += "x"

    invalid = client.get("/".join(tampered_parts))
    missing_feedback = client.post(
        "/api/v1/rag-runs/unknown/feedback",
        json={"rating": 1, "reason_code": "bad"},
    )

    assert invalid.status_code == 404
    assert invalid.json()["code"] == "SOURCE_REFERENCE_NOT_FOUND"
    assert missing_feedback.status_code == 404
    assert missing_feedback.json()["code"] == "NOT_FOUND"


def test_openapi_adds_ask_stream_source_and_feedback_without_agent_tools(tmp_path: Path) -> None:
    schema = create_app(_components(tmp_path)).openapi()

    for path in (
        "/api/v1/ask",
        "/api/v1/ask:stream",
        "/api/v1/rag-runs/{run_token}/evidence/{evidence_token}/source",
        "/api/v1/rag-runs/{rag_run_id}/feedback",
    ):
        assert path in schema["paths"]
    assert not any("agent" in path.casefold() for path in schema["paths"])
