from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from threading import BoundedSemaphore

import pytest
from fastapi.testclient import TestClient
from ragkb.adapters.deadline_http import DeadlineHttpClient
from ragkb.adapters.mysql_entity_store import EntityRow
from ragkb.adapters.mysql_lazy_state import LazyCollection
from ragkb.adapters.mysql_upload import MySQLUploadRepository, _empty_state
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.adapters.vector_indexing import ZillizSafeProjectionWriter
from ragkb.api.app import create_app
from ragkb.application.deadlines import bounded_slot, request_deadline
from ragkb.application.tracing import InMemoryTracer
from ragkb.contracts.jobs import QueueLeaseError
from ragkb.contracts.ports import ParsingDeferred
from ragkb.document_processing.isolated_parser import IsolatedNativeParser, UnconfiguredASRParser
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.errors import ProviderTimeout
from ragkb.domain.publication_policy import review_quality_error
from ragkb.infrastructure.ingestion_fencing import sqlite_ingestion_scope
from test_lifecycle_fact_source import _components, _PrincipalAuthenticator, _process_next, _upload
from test_trusted_qa import _evidence, _service


def test_reader_cannot_enumerate_draft_or_restricted_chunks(tmp_path):
    runtime = _components(tmp_path)
    admin = TestClient(create_app(runtime))
    document, version, _ = _upload(admin, runtime.space_id)
    _process_next(runtime, admin, version)
    principal = RequestPrincipal(runtime.tenant_id, "reader", ("reader",), (), "oidc", 0)
    reader = TestClient(
        create_app(replace(runtime, authenticator=_PrincipalAuthenticator(principal)))
    )
    assert reader.get(f"/api/v1/spaces/{runtime.space_id}/documents").json() == []
    assert reader.get(f"/api/v1/document-versions/{version}/chunks").status_code == 404
    restricted = admin.post(
        f"/api/v1/document-versions/{version}/review",
        headers={"Idempotency-Key": "restricted"},
        json={
            "decision": "APPROVED",
            "comment": "",
            "security_projection": {
                "visibility": "RESTRICTED",
                "classification_level": 2,
                "acl_scope_tokens": ["group:legal"],
            },
        },
    )
    assert restricted.status_code == 200
    assert (
        admin.post(
            f"/api/v1/document-versions/{version}:publish", headers={"Idempotency-Key": "publish"}
        ).status_code
        == 200
    )
    assert reader.get(f"/api/v1/spaces/{runtime.space_id}/documents").json() == []
    assert reader.get(f"/api/v1/document-versions/{version}/chunks").status_code == 404
    assert reader.get(f"/api/v1/documents/{document}").status_code == 404


def test_new_version_keeps_custom_space_and_old_review_replay_is_inert(tmp_path):
    runtime = _components(tmp_path)
    client = TestClient(create_app(runtime))
    space = client.post("/api/v1/spaces", json={"name": "other"}).json()["id"]
    document, version, _ = _upload(client, space)
    _process_next(runtime, client, version)

    def review(key, visibility, scopes):
        return client.post(
            f"/api/v1/document-versions/{version}/review",
            headers={"Idempotency-Key": key},
            json={
                "decision": "APPROVED",
                "comment": "",
                "security_projection": {
                    "visibility": visibility,
                    "classification_level": 0,
                    "acl_scope_tokens": scopes,
                },
            },
        )

    first = review("a", "TENANT", [])
    second = review("b", "RESTRICTED", ["group:legal"])
    assert first.status_code == second.status_code == 200
    assert review("a", "TENANT", []).json() == first.json()
    latest = runtime.repository.get_latest_review(version)
    assert latest["review_id"] == second.json()["review_id"]
    with runtime.database.connect() as connection:
        scopes = connection.execute(
            "SELECT visibility FROM retrieval_projections WHERE document_version_id=?", (version,)
        ).fetchall()
    assert scopes and all(row["visibility"] == "RESTRICTED" for row in scopes)
    etag = client.get(f"/api/v1/documents/{document}").headers["etag"]
    _, new_version, _ = _upload(client, space, key="new", document_id=document, document_etag=etag)
    assert runtime.repository.get_document_space(document) == space
    assert new_version != version


def test_verifier_time_revocation_cannot_emit_answer(tmp_path):
    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider((_evidence(),)))
    delegate = service.verifier

    class RevokingVerifier:
        revision = delegate.revision

        def verify(self, *args, **kwargs):
            result = delegate.verify(*args, **kwargs)
            service.permission.allowed = False
            return result

    service.verifier = RevokingVerifier()
    result = service.ask("保修期多久？", "tenant-1", "user-1")
    assert not result.verified and result.answer is None
    assert "POST_VERIFIER_PERMISSION_RECHECK_FAILED" in result.warnings


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"decision": "REJECTED"}, "PUBLICATION_REVIEW_NOT_APPROVED"),
        ({"quality_revision": "stale"}, "PUBLICATION_REVIEW_REVISION_MISMATCH"),
        ({"projection_applied": False}, "PUBLICATION_SECURITY_PROJECTION_PENDING"),
        ({"security_revision": None}, "PUBLICATION_SECURITY_REVIEW_REQUIRED"),
    ],
)
def test_same_publication_policy_rejects_invalid_reviews(change, expected):
    review = {
        "decision": "APPROVED",
        "quality_revision": "p1",
        "security_revision": "s1",
        "security_projection": {"visibility": "TENANT"},
        "projection_applied": True,
    }
    assert review_quality_error({"parser_revision": "p1"}, {**review, **change}) == expected
    assert (
        review_quality_error({"disposition": "BLOCKED_REAL_VALIDATION"}, review)
        == "PUBLICATION_QUALITY_BLOCKED_REAL_VALIDATION"
    )


def test_oidc_preflight_and_live_ignore_observability_failure(tmp_path, monkeypatch):
    runtime = _components(tmp_path)

    def failed(*args):
        raise ConnectionError("telemetry database down")

    monkeypatch.setattr(runtime.observability, "request_completed", failed)
    client = TestClient(create_app(runtime))
    response = client.options(
        "/api/v1/spaces",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert client.get("/health/live").status_code == 200
    assert client.get("/api/v1/spaces").status_code == 200
    response = client.get("/api/v1/spaces", headers={"Origin": "http://127.0.0.1:5173"})
    assert "ETag" in response.headers["access-control-expose-headers"]
    from ragkb.adapters.auth import AuthenticationError

    class RequiresBearer:
        def authenticate(self, authorization):
            raise AuthenticationError("token required")

    protected = TestClient(create_app(replace(runtime, authenticator=RequiresBearer())))
    assert protected.get("/api/v1/spaces").status_code == 401
    assert (
        protected.options(
            "/api/v1/spaces",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        ).status_code
        == 200
    )


def test_lazy_row_mutation_does_not_load_unrelated_collections():
    calls = []

    def load(**filters):
        calls.append(filters)
        return (
            {("documents", "doc"): EntityRow("doc", None, 0, {"id": "doc"}, 4)}
            if filters["entity_type"] == "documents"
            else {}
        )

    state = {kind: LazyCollection(kind, load) for kind in _empty_state()}
    state["documents"]["doc"]["state"] = "DELETED"
    entities = MySQLUploadRepository._to_entities(state)
    assert len(entities) == 1
    assert calls == [{"entity_type": "documents", "entity_id": "doc"}]


def test_uncertain_vector_write_requires_complete_payload_not_just_pk():
    expected = {
        "zilliz_pk": "pk",
        "content_checksum": "new",
        "acl_scope_tokens": ["legal"],
        "vector": [0.1, 0.2],
    }
    assert not ZillizSafeProjectionWriter._matches(expected, {"zilliz_pk": "pk"})
    assert not ZillizSafeProjectionWriter._matches(
        expected, {**expected, "acl_scope_tokens": ["public"]}
    )
    assert not ZillizSafeProjectionWriter._matches(
        expected, {**expected, "content_checksum": "old"}
    )
    assert ZillizSafeProjectionWriter._matches(expected, expected)


def test_deadline_includes_semaphore_wait_and_traces_are_bounded():
    semaphore = BoundedSemaphore(1)
    semaphore.acquire()
    started = time.monotonic()
    with pytest.raises(ProviderTimeout), request_deadline(0.02), bounded_slot(semaphore, 60):
        pytest.fail("must not start a provider call")
    assert time.monotonic() - started < 0.5
    tracer = InMemoryTracer(3)
    for i in range(10):
        with tracer.span(str(i)):
            pass
    assert len(tracer.completed) == 3
    assert tracer.completed[0].name == "7"


def test_isolated_native_parser_returns_real_content_and_audio_fails_closed(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("native parser evidence", encoding="utf-8")
    document = IsolatedNativeParser("txt").parse(path, "version")
    assert document.nodes[0].display_text == "native parser evidence"
    with pytest.raises(ParsingDeferred, match="audio requires"):
        UnconfiguredASRParser().parse(path, "version")


def test_slow_http_body_has_total_cancellable_deadline():
    import httpx

    cancelled = []

    async def handler(request):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return httpx.Response(200, json={})

    client = DeadlineHttpClient(transport=httpx.MockTransport(handler))
    started = time.monotonic()
    try:
        with pytest.raises(httpx.ReadTimeout):
            client.post("https://synthetic.invalid", timeout=httpx.Timeout(0.03))
        assert time.monotonic() - started < 0.5
        assert cancelled == [True]
    finally:
        client.close()


def test_slow_stream_and_queued_upload_release_capacity(tmp_path):
    from test_streaming_upload import _service, _session

    service, runtime = _service(tmp_path, max_bytes=20, quota_bytes=20)
    session = _session(service, runtime, b"abc")
    service.stream_timeout_seconds = 0.06
    service.stream_idle_timeout_seconds = 0.02

    async def body():
        yield b"a"
        await asyncio.sleep(10)

    async def exercise():
        with pytest.raises(TimeoutError):
            await service.upload_content_stream(
                session.id, body(), expected_row_version=1, content_length=3
            )
        assert runtime.storage._reserved_quarantine_bytes == 0
        await service._stream_slots.acquire()
        try:
            with pytest.raises(TimeoutError):
                await service.upload_content_stream(
                    session.id, body(), expected_row_version=1, content_length=3
                )
        finally:
            service._stream_slots.release()

    asyncio.run(exercise())
    assert not list(runtime.storage.root.rglob("*.uploading"))


def test_sqlite_fence_rejects_old_worker_after_takeover_and_manual_retry(tmp_path):
    runtime = _components(tmp_path)
    queue = runtime.queue
    job = queue.enqueue(
        "process_document",
        {"tenant_id": runtime.tenant_id, "document_version_id": "synthetic"},
        "fence",
        "hash",
        available_at=1,
    )
    old = queue.lease("old", now=1, lease_seconds=1)
    new = queue.lease("new", now=3, lease_seconds=10)
    assert old and new and new.fence_token > old.fence_token
    with (
        sqlite_ingestion_scope(old),
        pytest.raises(QueueLeaseError),
        runtime.database.transaction(),
    ):
        pytest.fail("old worker must not enter a write transaction")
    queue.fail(job.id, "new", "synthetic", retryable=False, now=4)
    queue.retry(job.id)
    retried = queue.lease("retry", now=time.time() + 1)
    assert retried and retried.fence_token > new.fence_token


def test_redis_enqueue_transaction_failure_is_recoverable():
    from ragkb.adapters.redis_queue import RedisPersistentJobQueue
    from test_redis_queue import _Redis

    redis = _Redis()
    queue = RedisPersistentJobQueue(redis)
    redis.client.fail_transaction = True
    with pytest.raises(ConnectionError):
        queue.enqueue("process", {}, "key", "hash")
    assert redis.client.hashes == {}
    redis.client.fail_transaction = False
    created = queue.enqueue("process", {}, "key", "hash")
    assert queue.enqueue("process", {}, "key", "hash").id == created.id


def test_cache_key_changes_with_verifier_revision(tmp_path):
    from ragkb.application.qa import verified_answer_cache_key

    service, repository, _ = _service(tmp_path, SyntheticEvidenceProvider((_evidence(),)))
    answer = service.ask("保修期多久？", "tenant-1", "user-1")
    package = repository.get_package(answer.rag_run_id)
    assert verified_answer_cache_key(package) != verified_answer_cache_key(
        replace(package, verifier_revision="different-verifier:v2")
    )


def test_scanner_failure_and_native_timeout_fail_closed(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    from ragkb.engineering_security.malware import SystemMalwareScanner

    path = tmp_path / "safe.txt"
    path.write_text("safe fixture", encoding="utf-8")
    scanner = SystemMalwareScanner(executable="synthetic-clamscan")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=2))
    assert not scanner.scan(path).clean
    scanner.executable = None
    assert scanner.scan(path).reason_code == "ANTIVIRUS_UNAVAILABLE"
    with pytest.raises(ParsingDeferred):
        IsolatedNativeParser("txt", timeout=0.001).parse(path, "version")


def test_expired_quarantine_is_reaped_but_original_is_preserved(tmp_path):
    import os

    from ragkb.adapters.local_storage import LocalFileStorage

    storage = LocalFileStorage(tmp_path / "storage")
    storage.ensure_layout()
    old = storage.write_bytes("quarantine", "upload-sessions/expired/sample.md", b"expired")
    original = storage.write_bytes("original", "sample.md", b"preserved")
    os.utime(old, (1, 1))
    os.utime(original, (1, 1))
    assert storage.cleanup_stale_uploads() == 1
    assert not old.exists() and original.exists()


def test_upload_session_ttl_rejects_expired_session(tmp_path):
    from test_streaming_upload import _chunks, _service, _session

    service, runtime = _service(tmp_path, max_bytes=20, quota_bytes=20)
    session = _session(service, runtime, b"abc")
    service.session_ttl_seconds = -1
    with pytest.raises(Exception, match="create a new upload session"):
        asyncio.run(
            service.upload_content_stream(
                session.id, _chunks(b"abc"), expected_row_version=1, content_length=3
            )
        )


def test_access_metrics_shutdown_drains_app_owned_work_and_rejects_new_work(tmp_path):
    from threading import Event

    from ragkb.application.access_telemetry import AccessTelemetry

    metrics = AccessTelemetry(capacity=1)
    started, release = Event(), Event()
    completed = []

    def slow_callback():
        started.set()
        assert release.wait(2)
        completed.append("slow")

    metrics.submit(slow_callback)
    assert started.wait(2)
    metrics.submit(completed.append, "queued")
    metrics.submit(completed.append, "overflow")
    assert metrics.snapshot()["dropped"] == 1
    release.set()
    assert metrics.close(2)
    metrics.submit(completed.append, "after-close")
    assert completed == ["slow", "queued"]
    assert metrics.snapshot() == {"pending": 0, "dropped": 2, "failed": 0}

    runtime = _components(tmp_path)
    app = create_app(runtime)
    with TestClient(app) as client:
        assert client.get("/api/v1/spaces").status_code == 200
    assert app.state.access_metrics.snapshot()["pending"] == 0
    assert app.state.access_metrics.close(0)
