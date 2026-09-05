"""Opt-in localhost MySQL/Redis tests; never call any model or cloud collection."""

from __future__ import annotations

import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.adapters.mysql_lifecycle import MySQLLifecycleStore
from ragkb.adapters.mysql_retrieval import MySQLRetrievalControlPlane
from ragkb.adapters.redis_cache import RedisCacheRateLimitAdapter
from ragkb.adapters.redis_queue import RedisPersistentJobQueue
from ragkb.application.lifecycle import LifecycleService
from ragkb.config.env import load_env
from ragkb.domain.retrieval import RetrievalRelease, SearchContext
from ragkb.domain.state_machines import UploadSessionState
from ragkb.domain.uploads import OptimisticConcurrencyError
from ragkb.infrastructure.mysql_migrations import apply_mysql_migrations
from test_mysql_retrieval import _chunk
from test_production_persistence_behaviors import upload

pytestmark = pytest.mark.skipif(
    os.environ.get("RAG_LOCAL_DB_TESTS") != "1",
    reason="explicit localhost service test opt-in required",
)


def test_real_mysql_transaction_fence_rejects_old_worker(local_services, tmp_path):
    from ragkb.contracts.jobs import QueueLeaseError
    from ragkb.infrastructure.ingestion_fencing import mysql_ingestion_scope

    mysql, redis = local_services
    repository, document, version, _ = upload(mysql, tmp_path)
    queue = RedisPersistentJobQueue(redis)
    queue.enqueue(
        "process_document",
        {"tenant_id": "tenant", "document_version_id": version},
        "fence-job",
        "hash",
        available_at=1,
    )
    old = queue.lease("old", now=1, lease_seconds=1)
    new = queue.lease("new", now=3, lease_seconds=30)
    assert old and new
    with mysql_ingestion_scope(mysql, old):

        def takeover():
            with mysql_ingestion_scope(mysql, new):
                repository.mark_version_failed(version, "new-owner")

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(takeover).result(10)
        with pytest.raises(QueueLeaseError, match="INGEST_FENCE_STALE"):
            repository.mark_version_processing(version)
    assert repository.get_version(version)["processing_state"] == "FAILED"


@pytest.fixture
def local_services():
    settings = load_env(Path(__file__).resolve().parents[2]).settings
    assert settings is not None
    assert settings.mysql_host in {"127.0.0.1", "localhost"}
    assert settings.redis_host in {"127.0.0.1", "localhost"}
    suffix = uuid.uuid4().hex[:12]
    database_name = f"ragkb_sr_{suffix}"
    prefix = f"ragkb:sr-test:{suffix}:"
    assert re.fullmatch(r"ragkb_sr_[a-f0-9]{12}", database_name)
    admin_control = MySQLControlPlaneAdapter(settings)
    admin = admin_control._connect(include_database=False)
    mysql = MySQLControlPlaneAdapter(settings.model_copy(update={"mysql_database": database_name}))
    redis = RedisCacheRateLimitAdapter(settings.model_copy(update={"redis_key_prefix": prefix}))
    created = False
    try:
        admin.cursor().execute(f"CREATE DATABASE `{database_name}`")
        created = True
        connection = mysql.connect()
        try:
            applied = apply_mysql_migrations(connection)
            assert applied["applied_count"] > 0
            assert apply_mysql_migrations(connection)["applied_count"] == 0
        finally:
            connection.close()
        assert redis._connected().ping()
        yield mysql, redis
    finally:
        mysql.close()
        if created:
            admin.cursor().execute(f"DROP DATABASE `{database_name}`")
            print(f"cleanup_mysql={database_name}:deleted")
        admin.close()
        client = redis._connected()
        keys = list(client.scan_iter(match=f"{prefix}*", count=100))
        assert all(key.startswith(prefix) for key in keys)
        if keys:
            client.delete(*keys)
        assert not list(client.scan_iter(match=f"{prefix}*"))
        client.close()
        print(f"cleanup_redis={prefix}:deleted")


def test_real_local_mysql_scoped_upload_generation_and_stale_revision(local_services, tmp_path):
    mysql, _ = local_services
    repository, document, version, _ = upload(mysql, tmp_path)
    assert repository.get_document_space(document) == "kb"
    assert repository.list_documents("kb")[0]["version_id"] == version
    pending = repository.pending_promoted_sessions()
    assert len(pending) == 1
    repository.update_session(
        pending[0].id,
        pending[0].row_version,
        UploadSessionState.COMPLETED,
        job_id="synthetic-recovered-job",
    )
    assert len(repository.pending_promoted_sessions()) == 1
    lifecycle = MySQLLifecycleStore(mysql, "tenant")
    LifecycleService(lifecycle, "tenant").register_document(document, version, trace_id="recovered")
    assert repository.pending_promoted_sessions() == []
    control = MySQLRetrievalControlPlane(mysql, "g1")
    chunk = replace(
        _chunk(),
        tenant_id="tenant",
        space_id="kb",
        document_id=document,
        document_version_id=version,
        index_generation_id="g1",
    )
    control.upsert_chunks([chunk])
    control.upsert_chunks([replace(chunk, index_generation_id="g2", display_text="future")])
    context = SearchContext("tenant", ("kb",), ("group:reader",), 1, 1, "g1", 2, 2)
    assert (
        control.authorize_chunks([chunk.chunk_id], context)[chunk.chunk_id].display_text
        == "display"
    )
    assert (
        control.authorize_chunks([chunk.chunk_id], replace(context, active_generation_id="g2"))[
            chunk.chunk_id
        ].display_text
        == "future"
    )
    control.set_release(RetrievalRelease("tenant", "kb", "g1", 2, 2))
    control.set_document_projection(
        document, active_version_id=version, lifecycle_projection="REVOKED", permission_revision=3
    )
    assert not control.authorize_chunks(
        [chunk.chunk_id], replace(context, active_permission_revision=3)
    )
    assert control.authorize_chunks([chunk.chunk_id], replace(context, active_generation_id="g2"))
    store = MySQLLifecycleStore(mysql, "tenant")
    store.reload()
    service = LifecycleService(store, "tenant")
    assert document in store.documents
    stale = MySQLLifecycleStore(mysql, "tenant")
    stale.reload()
    service.register_document("newer", "v2", trace_id="new")
    service.revoke(document, event_id="revoke", trace_id="local-test")
    stale.documents[document].visible = True
    with pytest.raises(OptimisticConcurrencyError):
        stale.persist_state(tenant_id="tenant")
    store.reload()
    assert "newer" in store.documents and not store.is_accessible(document)


def test_real_local_redis_atomic_concurrent_enqueue_and_historical_jobs(local_services):
    _, redis = local_services
    queue = RedisPersistentJobQueue(redis)
    with ThreadPoolExecutor(max_workers=8) as executor:
        jobs = list(executor.map(lambda _: queue.enqueue("process", {}, "same", "hash"), range(16)))
    assert len({job.id for job in jobs}) == 1
    leased = queue.lease("worker")
    assert leased is not None
    queue.complete(leased.id, "worker")
    assert queue.lease("other") is None
    redis._connected().hset(queue.idempotency_key, "process:dangling", "absent")
    recovered = queue.enqueue("process", {}, "dangling", "hash")
    assert queue.get(recovered.id) is not None
    for index in range(200):
        redis._connected().hset(queue.jobs_key, f"history-{index}", "not read during lease")
    assert queue.lease("next").id == recovered.id


def test_real_mysql_publish_rollback_and_permission_failure_recovery(
    local_services, tmp_path, monkeypatch
):
    from ragkb.adapters.repository_readiness import RepositoryPublicationReadiness
    from ragkb.document_processing.parsers import PlainTextParser
    from ragkb.domain.retrieval import SecurityProjection
    from ragkb.domain.validation import DocumentQualityReport

    mysql, _ = local_services
    repository, document_id, first, session = upload(mysql, tmp_path)
    control = MySQLRetrievalControlPlane(mysql, "g1")
    control.set_release(RetrievalRelease("tenant", "kb", "g1", 1, 1))
    store = MySQLLifecycleStore(mysql, "tenant")
    service = LifecycleService(
        store,
        "tenant",
        publication_readiness=RepositoryPublicationReadiness(repository),
        retrieval_projection=control,
    )
    service.register_document(document_id, first, trace_id="seed")

    def approve(version_id):
        report = repository.get_quality_report(version_id)
        security = SecurityProjection("TENANT", 0, (), "STAGED", 1, 0)
        chunk = replace(
            _chunk(),
            tenant_id="tenant",
            space_id="kb",
            chunk_id=version_id,
            document_id=document_id,
            document_version_id=version_id,
            index_generation_id="g1",
        )
        control.upsert_chunks([chunk])
        reviewed = repository.save_document_review(
            version_id=version_id,
            reviewer_id="reviewer",
            decision="APPROVED",
            comment="",
            quality_revision=report["parser_revision"],
            security_revision="s1",
            security_projection={"visibility": "TENANT"},
            idempotency_key=version_id,
            request_hash=version_id,
        )
        service.set_reviewed_security_projection(
            document_id, version_id, security, trace_id="review"
        )
        repository.mark_review_applied(version_id, reviewed["review_id"])

    approve(first)
    service.publish(document_id, first, event_id="p1", trace_id="publish")
    current = repository.get_document(document_id)
    second_session = repository.create_upload_session(
        tenant_id="tenant",
        space_id="kb",
        filename="new.txt",
        expected_size=session.expected_size,
        expected_sha256=session.expected_sha256,
        declared_mime="text/plain",
        idempotency_key="new",
        request_hash="new",
        target_document_id=document_id,
        target_document_row_version=current["row_version"],
    )
    _, second = repository.ensure_document_version(
        replace(second_session, original_key="original/new.txt", detected_mime="text/plain")
    )
    canonical = PlainTextParser("txt").parse(tmp_path / "sample.txt", second)
    repository.save_canonical_document(canonical)
    repository.mark_index_ready(second)
    repository.save_quality_report(DocumentQualityReport.from_document(canonical))
    approve(second)
    service.publish(document_id, second, event_id="p2", trace_id="publish")
    assert repository.publication_readiness(document_id, first, rollback=True).ready
    service.rollback(document_id, first, event_id="r1", trace_id="rollback")
    assert repository.get_document(document_id)["current_version_id"] == first
    assert repository.publication_readiness(document_id, second, rollback=True).ready
    policy = {
        "visibility": "RESTRICTED",
        "classification_level": 1,
        "acl_scope_tokens": ["group:legal"],
    }
    expected = store.documents[document_id].row_version
    original_projection = control.set_version_security_projection

    def fail_projection(*args, **kwargs):
        raise ConnectionError("simulated projection unavailable")

    monkeypatch.setattr(control, "set_version_security_projection", fail_projection)
    with pytest.raises(ConnectionError):
        service.replace_permissions(
            document_id, policy, expected_row_version=expected, event_id="acl", trace_id="failure"
        )
    assert not store.is_accessible(document_id)
    recovered = MySQLLifecycleStore(mysql, "tenant")
    recovered.reload()
    assert not recovered.is_accessible(document_id)
    monkeypatch.setattr(control, "set_version_security_projection", original_projection)
    resumed = LifecycleService(
        recovered,
        "tenant",
        publication_readiness=RepositoryPublicationReadiness(repository),
        retrieval_projection=control,
    )
    resumed.replace_permissions(
        document_id, policy, expected_row_version=expected, event_id="acl", trace_id="recovery"
    )
    assert recovered.is_accessible(document_id)
    resumed.rollback(document_id, second, event_id="rollback-after-acl", trace_id="rollback")
    context = SearchContext("tenant", ("kb",), (), 3, 2**31, "g1", 2, 2)
    assert not control.authorize_chunks([second], context)
    assert control.authorize_chunks(
        [second], replace(context, subject_scope_tokens=("group:legal",))
    )
