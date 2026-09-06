from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.qa import InMemoryVerifiedAnswerCache
from ragkb.domain.rag import AtomicClaim, DraftAnswer
from test_lifecycle_fact_source import _components, _process_next, _upload


class EchoGenerator:
    revision = "echo-for-release-test"
    calls = 0

    def generate(self, question, evidence):
        self.calls += 1
        text = evidence[0].text
        ids = (evidence[0].evidence_id,)
        return DraftAnswer(text, ids, (AtomicClaim(text, ids),))


class TrackingCache(InMemoryVerifiedAnswerCache):
    writes = 0

    def put(self, package, draft):
        super().put(package, draft)
        self.writes += 1


@pytest.fixture
def published_runtime(tmp_path, monkeypatch):
    for key, value in {
        "APP_ENV": "testing",
        "RAG_RUNTIME_PROFILE": "local",
        "VECTOR_BACKEND": "local",
        "AUTH_MODE": "local_single_user",
        "REAL_PROVIDER_CALLS_ENABLED": "false",
        "EXTERNAL_LIFECYCLE_MUTATIONS_ENABLED": "false",
        "OTEL_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    runtime = _components(tmp_path)
    client = TestClient(create_app(runtime))
    document, version, _ = _upload(client, runtime.space_id)
    _process_next(runtime, client, version)
    published = client.post(
        f"/api/v1/document-versions/{version}:publish", headers={"Idempotency-Key": "publish"}
    )
    assert published.status_code == 200
    runtime.qa_service.generator = EchoGenerator()
    runtime.qa_service.cache = TrackingCache()
    return runtime, client, document, published.json()["row_version"]


@pytest.mark.parametrize("path", ["/api/v1/ask", "/api/v1/ask:stream"])
@pytest.mark.parametrize("warm_cache", [False, True])
@pytest.mark.parametrize("mutation", ["revoke", "permissions"])
def test_verifier_wait_cannot_release_revoked_evidence(
    published_runtime, monkeypatch, path, warm_cache, mutation
):
    runtime, client, document, row_version = published_runtime
    service = runtime.qa_service
    if warm_cache:
        assert client.post("/api/v1/ask", json={"question": "fact source"}).json()["verified"]
    cache = service.cache
    before_writes = cache.writes
    before_calls = service.generator.calls
    entered, resume = Event(), Event()
    original_verify = service.verifier.verify
    reference_calls = []
    original_reference = service.references.source_url

    def waiting_verify(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        assert result.supported
        entered.set()
        assert resume.wait(10), "test did not release verifier"
        return result

    def reference(*args):
        reference_calls.append(args)
        return original_reference(*args)

    monkeypatch.setattr(service.verifier, "verify", waiting_verify)
    monkeypatch.setattr(service.references, "source_url", reference)

    def change_permissions():
        if mutation == "revoke":
            return client.post(
                f"/api/v1/documents/{document}:revoke", headers={"Idempotency-Key": "revoke"}
            )
        return client.put(
            f"/api/v1/resources/document/{document}/permissions",
            headers={"Idempotency-Key": "restrict", "If-Match": f'"{row_version}"'},
            json={
                "security_projection": {
                    "visibility": "RESTRICTED",
                    "classification_level": 0,
                    "acl_scope_tokens": ["group:other"],
                }
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        answer_future = pool.submit(client.post, path, json={"question": "fact source"})
        try:
            assert entered.wait(10), "verifier was not reached"
            changed = pool.submit(change_permissions).result(timeout=5)
            assert changed.status_code == 200, changed.text
        finally:
            resume.set()
        response = answer_future.result(timeout=10)

    assert response.status_code == 200
    if path.endswith(":stream"):
        assert 'event: progress\ndata: {"stage": "verified"}' not in response.text
        payload = response.text.split("event: result\ndata: ", 1)[1].strip()
        result = json.loads(payload)
    else:
        result = response.json()
    assert result["status"] == "system_error"
    assert result["verified"] is False
    assert result["answer"] is None and result["citations"] == []
    assert result["warnings"] == ["FINAL_PERMISSION_RECHECK_FAILED"]
    assert "fact source fact" not in response.text
    assert cache.writes == before_writes
    assert reference_calls == []
    assert service.generator.calls == before_calls + (0 if warm_cache else 1)
    assert runtime.rag_repository.get_result(result["rag_run_id"]).verified is False


def test_release_and_revoke_share_one_application_boundary(published_runtime, monkeypatch):
    runtime, client, document, _ = published_runtime
    service = runtime.qa_service
    writing, resume, revoke_started = Event(), Event(), Event()
    order = []
    original_put = service.cache.put
    original_save = service.repository.save_run

    def waiting_put(*args):
        writing.set()
        assert resume.wait(10), "test did not release cache write"
        original_put(*args)
        order.append("cache_written")

    def save(*args):
        original_save(*args)
        order.append("run_saved")

    def revoke():
        revoke_started.set()
        runtime.lifecycle_service.revoke(document, event_id="revoke", trace_id="test")
        order.append("revoked")

    monkeypatch.setattr(service.cache, "put", waiting_put)
    monkeypatch.setattr(service.repository, "save_run", save)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answer_future = pool.submit(client.post, "/api/v1/ask", json={"question": "fact source"})
        try:
            assert writing.wait(10), "cache write was not reached"
            acquired = runtime.lifecycle_store.lock.acquire(blocking=False)
            if acquired:
                runtime.lifecycle_store.lock.release()
            assert not acquired, "release must hold the lifecycle mutation lock"
            revoked = pool.submit(revoke)
            assert revoke_started.wait(5)
        finally:
            resume.set()
        response = answer_future.result(timeout=10)
        revoked.result(timeout=10)
    assert response.json()["verified"] is True
    assert order == ["cache_written", "run_saved", "revoked"]
    assert client.get(response.json()["citations"][0]["source_url"]).status_code == 404
    subsequent = client.post("/api/v1/ask", json={"question": "fact source"}).json()
    assert subsequent["answer"] is None and subsequent["citations"] == []
