from __future__ import annotations

import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient
from ragkb.adapters.conversation_context import (
    bounded_history,
)
from ragkb.api.app import create_app
from ragkb.application.provider_budget import ConservativeTokenCounter
from ragkb.application.worker import LocalIngestionWorker
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.rag import AtomicClaim, DraftAnswer
from ragkb.domain.uploads import ResourceNotFoundError
from ragkb.infrastructure.conversations import ConversationBusy
from ragkb.runtime_components import build_runtime_components


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for key, value in {
        "APP_ENV": "testing",
        "RAG_RUNTIME_PROFILE": "local",
        "VECTOR_BACKEND": "local",
        "AUTH_MODE": "local_single_user",
        "OTEL_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    runtime = build_runtime_components(
        storage_root=tmp_path / "storage", database_path=tmp_path / "db.sqlite3"
    )
    app = create_app(runtime)
    with TestClient(app) as client:
        space = client.post("/api/spaces", json={"name": "工作台测试库"}).json()["id"]
        yield client, runtime, app.state.conversation_service, space


def upload(client, runtime, space, name="service.md", body=None, process=True):
    content = (body or "# 服务政策\n星云 X1 的保修期为三年。星云 X1 支持上门维修服务。\n").encode()
    created = client.post(
        f"/api/spaces/{space}/upload-sessions",
        headers={"Idempotency-Key": name},
        json={
            "filename": name,
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "declared_mime": "text/markdown",
        },
    )
    assert created.status_code == 201, created.text
    sent = client.put(
        created.json()["upload_path"],
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    done = client.post(
        f"/api/upload-sessions/{created.json()['upload_session_id']}:complete",
        headers={"If-Match": sent.headers["etag"], "Idempotency-Key": "complete-" + name},
    )
    assert done.status_code == 202, done.text
    if process:
        worker = LocalIngestionWorker(
            runtime.queue,
            runtime.repository,
            runtime.storage,
            runtime.parser_router,
            "workspace-tests",
            chunker=runtime.chunker,
            indexing_sink=runtime.indexing_sink,
        )
        assert worker.run_once()
    return done.json()


def publish(client, version):
    result = client.post(
        f"/api/document-versions/{version}:review-and-publish",
        headers={"Idempotency-Key": "publish-" + version},
        json={"comment": "检查质量后发布"},
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_visual_reparse_creates_new_version_and_is_idempotent(workspace):
    from dataclasses import replace

    client, runtime, _, space = workspace
    done = upload(client, runtime, space, name="reparse.md")
    publish(client, done["document_version_id"])
    updated_runtime = replace(
        runtime, settings=runtime.settings.model_copy(update={"ocr_enabled": True})
    )
    with TestClient(create_app(updated_runtime)) as updated:
        headers = {
            "If-Match": str(
                client.get(f"/api/documents/{done['document_id']}/preview").json()["row_version"]
            ),
            "Idempotency-Key": "reparse-one",
        }
        path = f"/api/document-versions/{done['document_version_id']}:reparse-visuals"
        response = updated.post(path, headers=headers)
        assert response.status_code == 202, response.text
        assert response.json()["document_version_id"] != done["document_version_id"]
        replay = updated.post(path, headers=headers)
        assert replay.status_code == 202 and replay.json() == response.json()
        current = updated.get(f"/api/documents/{done['document_id']}/lifecycle").json()
        assert current["active_version_id"] == done["document_version_id"]


def test_visual_manifest_preview_and_publication_block(workspace):
    import io

    from PIL import Image
    from ragkb.domain.validation import DocumentQualityReport, QualityDisposition
    from ragkb.infrastructure.visual_assets import VisualAssetStore

    client, runtime, _, space = workspace
    done = upload(client, runtime, space, name="pending-visual.md")
    version = done["document_version_id"]
    data = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(data, format="PNG")
    store = VisualAssetStore(runtime.storage)
    asset = store.save_image(version, data.getvalue(), {"part": "image.png"})
    asset.update(status="needs_review", issues=["箭头模糊"], extraction=None)
    store.write_manifest(version, [asset])
    quality = runtime.repository.get_quality_report(version)
    report = DocumentQualityReport(
        version,
        quality["source_format"],
        quality["parser_revision"],
        quality["node_count"],
        quality["locator_coverage"],
        ("VISUAL_REVIEW_REQUIRED:" + asset["id"],),
        QualityDisposition.BLOCKED_REAL_VALIDATION,
    )
    runtime.repository.save_quality_report(report)
    response = client.get(f"/api/document-versions/{version}/visuals")
    assert response.status_code == 200 and response.json()["review_count"] == 1
    public = response.json()["items"][0]
    assert "storage_key" not in public and "audit" not in public
    assert client.get(public["image_url"]).content == data.getvalue()
    publish_response = client.post(
        f"/api/document-versions/{version}:review-and-publish",
        headers={"Idempotency-Key": "blocked-visual"},
        json={},
    )
    assert publish_response.status_code == 409
    assert (
        client.get(f"/api/document-versions/{version}/visuals/{'a' * 32}/image").status_code == 404
    )


def test_signed_image_reference_expires_on_document_revocation(workspace, tmp_path, monkeypatch):
    from ragkb.adapters.visual_http import VisualAnalyzer
    from ragkb.document_processing.parsers import ImageParserRoute, ParserRouter
    from ragkb.document_processing.visual_parser import VisualDocumentParser
    from ragkb.infrastructure.visual_assets import VisualAssetStore
    from test_visual_pipeline import EXTRACTION, PASS, Transport, png, settings

    client, runtime, _, space = workspace
    data = png()
    session = runtime.uploads.create_session(
        space_id=space,
        filename="diagram.png",
        expected_size=len(data),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        declared_mime="image/png",
        idempotency_key="image-upload",
    )
    session = runtime.uploads.upload_content(
        session.id, data, expected_row_version=session.row_version
    )
    done = runtime.uploads.complete(
        session.id, expected_row_version=session.row_version, idempotency_key="image-complete"
    )
    runtime.lifecycle_service.register_document(
        done["document_id"], done["document_version_id"], trace_id="visual-source-test"
    )
    parser = VisualDocumentParser(
        ImageParserRoute(),
        "image",
        VisualAnalyzer(settings(), Transport(EXTRACTION, PASS)),
        VisualAssetStore(runtime.storage),
        settings(),
    )
    worker = LocalIngestionWorker(
        runtime.queue,
        runtime.repository,
        runtime.storage,
        ParserRouter({"image": parser}),
        "visual-source-worker",
        chunker=runtime.chunker,
        indexing_sink=runtime.indexing_sink,
    )
    assert worker.run_once()
    publish(client, done["document_version_id"])

    def generate(question, evidence):
        item = next(e for e in evidence if "网关与订单服务双向连接。" in e.text)
        return DraftAnswer(
            "网关与订单服务双向连接。",
            (item.evidence_id,),
            (AtomicClaim("网关与订单服务双向连接。", (item.evidence_id,)),),
        )

    monkeypatch.setattr(runtime.qa_service.generator, "generate", generate)
    response = client.post(
        "/api/ask", json={"question": "网关如何与订单服务连接？", "space_id": space}
    )
    assert response.status_code == 200 and response.json()["verified"], response.text
    source_url = response.json()["citations"][0]["source_url"]
    source = client.get(source_url)
    assert source.status_code == 200 and source.json()["visuals"]
    image_url = source.json()["visuals"][0]["image_url"]
    assert client.get(image_url).content == data
    assert client.get(image_url.replace("/image", "/../image")).status_code != 200
    revoked = client.post(
        f"/api/documents/{done['document_id']}:revoke", headers={"Idempotency-Key": "revoke-visual"}
    )
    assert revoked.status_code == 200
    assert client.get(source_url).status_code == 404
    assert client.get(image_url).status_code == 404
    store = VisualAssetStore(runtime.storage)
    original_asset = store.list_assets(done["document_version_id"])[0]
    deleted = client.delete(
        f"/api/documents/{done['document_id']}", headers={"Idempotency-Key": "delete-visual"}
    )
    assert deleted.status_code == 200
    cleaned = client.post(
        f"/api/documents/{done['document_id']}/cleanup/local_file:run",
        headers={"Idempotency-Key": "cleanup-visual"},
    )
    assert cleaned.status_code == 200
    assert not runtime.storage.exists("artifacts", original_asset["storage_key"])
    assert not runtime.storage.exists("artifacts", store.manifest_key(done["document_version_id"]))


def test_overview_preview_filters_download_and_publish(workspace):
    client, runtime, _, space = workspace
    caps = client.get("/api/capabilities").json()
    assert ".md" in caps["accepted_extensions"] and ".mp3" not in caps["accepted_extensions"]
    assert caps["max_file_size_bytes"] > 0
    done = upload(client, runtime, space)
    version, doc = done["document_version_id"], done["document_id"]
    before = client.get(f"/api/spaces/{space}/documents/preview").json()
    assert before[0]["availability"] == "pending_review"
    assert "review_publish" in before[0]["available_actions"]
    quality = client.get(f"/api/spaces/{space}/documents/{doc}/workspace")
    assert quality.status_code == 200, quality.text
    assert quality.json()["quality"]["node_count"] > 0
    assert client.get(f"/api/document-versions/{version}/original/preview").content.startswith(
        b"# "
    )
    assert publish(client, version)["phase"] == "published"
    assert publish(client, version)["phase"] == "published"
    after = client.get(f"/api/spaces/{space}/documents/preview?availability=available").json()
    assert len(after) == 1 and after[0]["is_answerable"]
    assert client.get(f"/api/spaces/{space}/documents/preview?q=absent").json() == []
    overview = client.get("/api/spaces/overview").json()
    item = next(row for row in overview["items"] if row["id"] == space)
    assert item["document_count"] == 1 and item["answerable_count"] == 1
    assert (
        client.patch(
            f"/api/spaces/{space}", json={"name": "更名测试库", "description": "真实描述"}
        ).status_code
        == 200
    )
    assert (
        next(
            row for row in client.get("/api/spaces/overview").json()["items"] if row["id"] == space
        )["description"]
        == "真实描述"
    )
    revoked = client.post(
        f"/api/documents/{doc}:revoke", headers={"Idempotency-Key": "revoke-test"}
    )
    assert revoked.status_code == 200
    after = client.get(f"/api/spaces/{space}/documents/preview").json()[0]
    assert after["availability"] == "withdrawn" and not after["is_answerable"]


def test_publication_retry_preserves_review_phase(workspace, monkeypatch):
    client, runtime, _, space = workspace
    done = upload(client, runtime, space)
    original = runtime.lifecycle_service.publish

    def unavailable(*args, **kwargs):
        raise RuntimeError("TEMPORARY_INDEX_FAILURE")

    monkeypatch.setattr(runtime.lifecycle_service, "publish", unavailable)
    first = publish(client, done["document_version_id"])
    assert first["phase"] == "reviewed" and first["retryable"]
    restored = client.get(f"/api/spaces/{space}/documents/{done['document_id']}/workspace").json()
    assert restored["publication"]["phase"] == "reviewed"
    review_id = first["review_id"]
    monkeypatch.setattr(runtime.lifecycle_service, "publish", original)
    second = publish(client, done["document_version_id"])
    assert second["phase"] == "published" and second["review_id"] == review_id


def test_task_list_filter_cursor_cancel_and_recovery(workspace):
    client, runtime, _, space = workspace
    jobs = [upload(client, runtime, space, name=f"queued-{i}.md", process=False) for i in range(3)]
    page = client.get(f"/api/ingestion-jobs?space_id={space}&state=QUEUED&limit=2")
    assert page.status_code == 200, page.text
    assert len(page.json()) == 2 and page.headers["X-Next-Cursor"]
    second = client.get(
        "/api/ingestion-jobs",
        params={
            "space_id": space,
            "state": "QUEUED",
            "limit": 2,
            "cursor": page.headers["X-Next-Cursor"],
        },
    )
    assert second.status_code == 200, second.text
    assert len(second.json()) == 1
    assert len({item["id"] for item in page.json() + second.json()}) == 3
    job_id = jobs[0]["job_id"]
    job = client.get(f"/api/ingestion-jobs/{job_id}")
    cancelled = client.post(
        f"/api/ingestion-jobs/{job_id}:cancel",
        headers={"If-Match": job.headers["etag"], "Idempotency-Key": "cancel-task"},
    )
    assert cancelled.status_code == 202, cancelled.text
    counts = client.get(f"/api/ingestion-jobs/summary?space_id={space}").json()["counts"]
    assert counts["QUEUED"] == 2 and counts["CANCELLED"] == 1


def stream_result(client, conversation, question, key):
    response = client.post(
        f"/api/conversations/{conversation}/turns:stream",
        json={"question": question, "client_request_id": key},
    )
    assert response.status_code == 200, response.text
    frames = response.text.split("\n\n")
    results = [
        json.loads(frame.split("data: ", 1)[1])
        for frame in frames
        if frame.startswith("event: result")
    ]
    assert len(results) == 1, response.text
    return results[0]


def test_durable_dialogue_context_dedup_and_stale_history(workspace, monkeypatch):
    client, runtime, service, space = workspace
    original_generate = runtime.qa_service.generator.generate

    def generate(question, evidence):
        if "上门" in question:
            assert "星云 X1" in question
            source = next(item for item in evidence if "支持上门" in item.text)
            answer = "星云 X1 支持上门维修服务。"
            citations = (source.evidence_id,)
            return DraftAnswer(answer, citations, (AtomicClaim(answer, citations),))
        return original_generate(question, evidence)

    monkeypatch.setattr(runtime.qa_service.generator, "generate", generate)
    done = upload(client, runtime, space)
    publish(client, done["document_version_id"])
    conversation = client.post(
        "/api/conversations",
        headers={"Idempotency-Key": "conversation-a"},
        json={"space_id": space},
    ).json()["id"]
    first = stream_result(client, conversation, "星云 X1 保修多久？", "turn-a")
    assert first["result"]["verified"] and "三年" in first["result"]["answer"], first
    assert stream_result(client, conversation, "星云 X1 保修多久？", "turn-a")["id"] == first["id"]
    second = stream_result(client, conversation, "它支持上门维修吗？", "turn-b")
    assert "星云 X1" in second["resolved_question"], second
    assert second["result"]["verified"] and "支持上门" in second["result"]["answer"], second
    assert second["rag_run_id"] != first["rag_run_id"]
    history = client.get(f"/api/conversations/{conversation}").json()
    assert len(history["turns"]) == 2
    assert (
        client.get(history["turns"][0]["result"]["citations"][0]["source_url"]).status_code == 200
    )
    client.post(
        f"/api/documents/{done['document_id']}:revoke",
        headers={"Idempotency-Key": "history-revoke"},
    )
    stale = client.get(f"/api/conversations/{conversation}").json()["turns"][0]["result"]
    assert stale["sources_stale"] and stale["answer"] is None and stale["citations"] == []
    another = client.post(
        "/api/conversations",
        headers={"Idempotency-Key": "conversation-b"},
        json={"space_id": space},
    ).json()["id"]
    isolated = stream_result(client, another, "它支持上门维修吗？", "other-turn")
    assert isolated["result"]["answer"] is None


def test_single_turn_atomicity_cancellation_and_restart_recovery(workspace):
    client, runtime, service, space = workspace
    repository = service.repository
    subject = RequestPrincipal(
        runtime.tenant_id, "isolated-user", ("admin",), (), "local_single_user"
    )
    conversation = repository.create(space, "新对话", subject, "repo-atomic")
    first = repository.submit(conversation["id"], subject, "question", "same")
    assert repository.submit(conversation["id"], subject, "question", "same")["id"] == first["id"]
    with pytest.raises(ConversationBusy):
        repository.submit(conversation["id"], subject, "other", "new")
    with pytest.raises(ResourceNotFoundError):
        repository.get(
            conversation["id"],
            RequestPrincipal(runtime.tenant_id, "other", ("admin",), (), "local_single_user"),
        )
    assert repository.claim(first["id"], "execution")
    assert not repository.claim(first["id"], "duplicate")
    repository.cancel(first["id"], subject)
    repository.finish(
        first["id"], "execution", "completed", {"verified": True, "answer": "must not escape"}
    )
    assert repository.turn(first["id"])["result_json"] is None
    second = repository.submit(conversation["id"], subject, "restart", "second")
    with repository.db.transaction() as connection:
        repository.db.execute(
            connection,
            "UPDATE conversation_turns SET lease_expires_at=? WHERE id=?",
            (time.time() - 1, second["id"]),
        )
    assert repository.recover() == 1
    assert repository.get(conversation["id"], subject)["active_turn_id"] is None
    assert repository.turn(second["id"])["error_code"] == "EXECUTION_INTERRUPTED"


def test_context_budget_is_bounded_and_keeps_recent_six():
    history = [
        {"question": str(i), "resolved_question": "subject", "answer": "中文内容" * 5000}
        for i in range(12)
    ]
    selected = bounded_history(history)
    assert len(selected) <= 6 and selected[-1]["question"] == "11"
    assert ConservativeTokenCounter().count(json.dumps(selected, ensure_ascii=False)) <= 6000
