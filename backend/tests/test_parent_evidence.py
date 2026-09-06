from __future__ import annotations

import json
from dataclasses import asdict, replace

import pytest
from fastapi.testclient import TestClient
from ragkb.adapters.rag_stubs import LifecycleAwareFinalPermission
from ragkb.adapters.retrieval_memory import InMemoryHybridIndex, InMemoryRetrievalControlPlane
from ragkb.api.app import create_app
from ragkb.domain.rag import AnswerStatus, AtomicClaim, DraftAnswer, Evidence
from ragkb.domain.retrieval import IndexCandidate, RetrievalRelease
from test_generation_outcomes import generator_for
from test_search_backed_qa import _components_with_search_qa


def _components(tmp_path):
    components = _components_with_search_qa(tmp_path)
    search = components.search_service
    original = search.control_plane._chunks["qa-chunk"]
    child = replace(original, parent_chunk_id="parent", locator={"page": 2})
    other = replace(
        child,
        chunk_id="battery",
        display_text="电池容量为五千毫安时。",
        retrieval_text="电池容量 五千毫安时",
        content_checksum="battery",
        locator={"page": 3},
    )
    parent = replace(
        original,
        chunk_id="parent",
        display_text="退款期限为三十天。",
        retrieval_text="退款期限 三十天",
        content_checksum="parent",
        locator={
            "page": 1,
            "source_spans": [
                {"chunk_id": "refund", "locator": {"page": 1}},
                {"chunk_id": child.chunk_id, "locator": child.locator},
                {"chunk_id": other.chunk_id, "locator": other.locator},
            ],
        },
    )
    control = InMemoryRetrievalControlPlane(
        {item.chunk_id: item for item in (child, other, parent)}
    )
    search.control_plane = control
    search.index = InMemoryHybridIndex(
        bm25=tuple(
            IndexCandidate(
                item.chunk_id,
                item.document_version_id,
                item.parent_chunk_id,
                "bm25",
                index,
                1 / index,
            )
            for index, item in enumerate((child, other), start=1)
        ),
        security_watermark=0,
    )

    class _Release:
        def current_release(self, tenant_id, space_id):
            return RetrievalRelease(tenant_id, space_id, "local-test-generation", 1, 0)

    components.qa_service.permission = LifecycleAwareFinalPermission(
        components.lifecycle_store,
        components.tenant_id,
        control_plane=control,
        document_space=lambda _document: components.space_id,
        release=_Release(),
    )
    return components, control


def _package(components):
    return components.qa_service.evidence_provider.build_package(
        "退款期限多久？",
        components.tenant_id,
        "local-admin",
        clearance_level=3,
    )


def test_shared_parent_is_one_separately_located_evidence_in_model_input(tmp_path) -> None:
    components, _ = _components(tmp_path)
    package = _package(components)

    assert [item.chunk_id for item in package.evidence] == ["qa-chunk", "battery", "parent"]
    assert [item.evidence_id for item in package.evidence] == ["E1", "E2", "E3"]
    assert [item.source_role for item in package.evidence] == ["hit", "hit", "parent_context"]
    assert [item.parent_chunk_id for item in package.evidence] == ["parent", "parent", None]
    assert [item.locator["page"] for item in package.evidence] == [2, 3, 1]
    assert all("退款" not in item.text for item in package.evidence[:2])
    generator, transport = generator_for(
        tmp_path,
        {
            "status": "answered",
            "answer": "退款期限三十天",
            "citation_ids": ["E3"],
            "claims": [{"text": "退款期限三十天", "evidence_ids": ["E3"]}],
        },
    )
    generator.generate(package.query, package.evidence)
    rendered = transport.calls[0]["payload"]["messages"][1]["content"]
    payload = json.loads(rendered.split("UNTRUSTED_RETRIEVED_EVIDENCE_JSON:\n", 1)[1])
    assert sum(item["text"].count("退款期限 三十天") for item in payload) == 1
    assert "退款期限为三十天。" not in rendered
    assert payload[2]["locator"] == package.evidence[2].locator


@pytest.mark.parametrize("also_a_hit", [False, True])
def test_parent_adds_no_duplicate_when_its_text_is_already_a_hit(tmp_path, also_a_hit) -> None:
    components, control = _components(tmp_path)
    parent = control._chunks["parent"]
    if also_a_hit:
        components.search_service.index = InMemoryHybridIndex(
            bm25=(
                IndexCandidate("qa-chunk", "qa-version", "parent", "bm25", 1, 1.0),
                IndexCandidate("parent", "qa-version", None, "bm25", 2, 0.5),
            ),
            security_watermark=0,
        )
    else:
        control._chunks["qa-chunk"] = replace(
            control._chunks["qa-chunk"],
            retrieval_text=parent.retrieval_text,
            display_text=parent.display_text,
        )

    package = _package(components)

    assert len(package.evidence) == 2
    assert all(item.source_role == "hit" for item in package.evidence)
    assert sum(item.text.count(parent.retrieval_text) for item in package.evidence) == 1


@pytest.mark.parametrize("citation", ["E1", "E3"])
def test_parent_fact_requires_parent_citation_and_source_survives_persistence(
    tmp_path,
    citation,
) -> None:
    components, control = _components(tmp_path)

    class _Generator:
        revision = "parent-fact-test"

        def generate(self, question, evidence):
            return DraftAnswer(
                "退款期限三十天", (citation,), (AtomicClaim("退款期限三十天", (citation,)),)
            )

    components.qa_service.generator = _Generator()
    client = TestClient(create_app(components))
    answer = client.post("/api/ask", json={"question": "退款期限多久？"}).json()
    if citation == "E1":
        assert answer["status"] == AnswerStatus.INSUFFICIENT_EVIDENCE
        assert answer["citations"] == []
        assert "EXACT_FACT_NOT_IN_EVIDENCE" in answer["warnings"]
        return
    assert answer["status"] == AnswerStatus.ANSWERED
    assert answer["verified"] is True
    assert answer["citations"][0]["locator"]["page"] == 1
    source = client.get(answer["citations"][0]["source_url"])
    assert source.status_code == 200
    assert source.json()["text"] == "退款期限为三十天。"
    assert [span["locator"]["page"] for span in source.json()["locator"]["source_spans"]] == [
        1,
        2,
        3,
    ]
    stored = components.rag_repository.get_package(answer["rag_run_id"])
    assert stored is not None
    assert stored.evidence[2].source_role == "parent_context"
    assert stored.evidence[0].parent_chunk_id == stored.evidence[2].chunk_id
    control._chunks["parent"] = replace(
        control._chunks["parent"],
        visibility="RESTRICTED",
        acl_scope_tokens=("group:private",),
    )
    assert client.get(answer["citations"][0]["source_url"]).status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        {"acl_scope_tokens": ("group:private",), "visibility": "RESTRICTED"},
        {"classification_level": 4},
        {"current_version": False},
        {"valid_to_epoch": 1},
        {"document_version_id": "wrong-version"},
        {"document_id": "wrong-document"},
        {"locator": {"page": 1}},
    ],
)
def test_unreadable_or_unlocatable_parent_never_enters_evidence(tmp_path, change) -> None:
    components, control = _components(tmp_path)
    control._chunks["parent"] = replace(control._chunks["parent"], **change)

    package = _package(components)

    assert [item.chunk_id for item in package.evidence] == ["qa-chunk", "battery"]
    assert all("退款" not in item.text for item in package.evidence)


def test_parent_acl_is_rechecked_after_generation(tmp_path) -> None:
    components, control = _components(tmp_path)

    class _Generator:
        revision = "revoke-parent-test"

        def generate(self, question, evidence):
            control._chunks["parent"] = replace(
                control._chunks["parent"],
                visibility="RESTRICTED",
                acl_scope_tokens=("group:private",),
            )
            return DraftAnswer("退款期限三十天", ("E3",), (AtomicClaim("退款期限三十天", ("E3",)),))

    components.qa_service.generator = _Generator()
    answer = (
        TestClient(create_app(components))
        .post(
            "/api/ask",
            json={"question": "退款期限多久？"},
        )
        .json()
    )

    assert answer["status"] == AnswerStatus.SYSTEM_ERROR
    assert answer["answer"] is None
    assert answer["citations"] == []
    assert answer["warnings"] == ["PRE_VERIFIER_PERMISSION_RECHECK_FAILED"]


def test_search_response_keeps_parent_location_separate_from_hit(tmp_path) -> None:
    components, _ = _components(tmp_path)
    response = TestClient(create_app(components)).post(
        "/api/search",
        json={"query": "退款期限"},
    )

    assert response.status_code == 200
    hit = response.json()["hits"][0]
    assert hit["locator"]["page"] == 2
    assert hit["parent_source"]["locator"]["page"] == 1
    assert "退款" not in hit["generation_context"]


def test_legacy_evidence_remains_readable_without_source_fields(tmp_path) -> None:
    components, _ = _components(tmp_path)
    data = asdict(_package(components).evidence[0])
    for key in ("source_role", "parent_chunk_id", "display_text"):
        data.pop(key)

    legacy = Evidence(**data)

    assert legacy.source_role == "hit"
    assert legacy.parent_chunk_id is None
    assert legacy.display_text == ""
