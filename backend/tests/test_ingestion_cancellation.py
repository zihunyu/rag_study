from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from ragkb.adapters.provider_http import MinerUHttpTransport
from ragkb.api.app import create_app
from ragkb.application.cancellation import cancellation_scope
from ragkb.application.provider_runners import MinerUExecutionRunner
from ragkb.application.worker import LocalIngestionWorker
from ragkb.contracts.jobs import QueueLeaseError
from ragkb.contracts.provider_execution import ProviderExecutionError
from ragkb.document_processing import isolated_parser
from ragkb.document_processing.parsers import ParserRouter
from ragkb.domain.errors import IngestionCancelled
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore
from test_provider_runners import _MinerUTransport, _pool, _result_store, _source
from test_worker_resilience import _components, _enqueue_text


def _blocked_native(source_format, path, version_id, sender, timeout, workdir):
    """Top-level spawn target: it never cooperates with the parent's cancel signal."""
    Path(path + ".parsing").write_text(json.dumps({"pid": os.getpid(), "workdir": workdir}))
    time.sleep(10)  # Test cleanup bound only; cancellation must terminate before this returns.
    sender.close()


@pytest.mark.parametrize("interruption", ["cancel", "lease_lost"])
def test_blocked_native_process_is_terminated_before_it_returns(
    tmp_path, monkeypatch, interruption
):
    runtime = _components(tmp_path)
    job = _enqueue_text(runtime, filename="blocked.txt", content=b"blocked body", key="blocked")
    job_id, version_id = str(job["job_id"]), str(job["document_version_id"])
    version = runtime.repository.get_version(version_id)
    source = runtime.storage.path_for("original", str(version["original_key"]))
    ready = Path(str(source) + ".parsing")
    monkeypatch.setattr(isolated_parser, "_parse", _blocked_native)
    lease_lost = threading.Event()
    heartbeat = runtime.queue.heartbeat

    def renew(*args, **kwargs):
        if lease_lost.is_set():
            raise QueueLeaseError("new owner has acquired the job")
        return heartbeat(*args, **kwargs)

    monkeypatch.setattr(runtime.queue, "heartbeat", renew)
    worker = LocalIngestionWorker(
        runtime.queue,
        runtime.repository,
        runtime.storage,
        ParserRouter(),
        "cancel-native",
        lease_seconds=30,
    )
    errors = []

    def run():
        try:
            worker.run_once()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 6
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), errors
        child = json.loads(ready.read_text())
        assert child["pid"] in {process.pid for process in multiprocessing.active_children()}
        if interruption == "cancel":
            client = TestClient(create_app(runtime))
            running = client.get(f"/api/ingestion-jobs/{job_id}")
            response = client.post(
                f"/api/ingestion-jobs/{job_id}:cancel",
                headers={
                    "If-Match": running.headers["etag"],
                    "Idempotency-Key": "cancel-native",
                },
            )
            assert response.status_code == 202
        else:
            lease_lost.set()
        started = time.monotonic()
        thread.join(timeout=3)
        assert not thread.is_alive(), "worker waited for the blocked parser to finish"
        assert time.monotonic() - started < 3
        assert not errors
        assert child["pid"] not in {process.pid for process in multiprocessing.active_children()}
        assert not Path(child["workdir"]).exists()
        if interruption == "cancel":
            assert runtime.queue.get(job_id).state.value == "CANCELLED"
            assert runtime.repository.get_version(version_id)["processing_state"] == "CANCELLED"
        else:
            assert worker.last_failure.error_code == "INGEST_LEASE_LOST"
            assert runtime.repository.get_version(version_id) == version
        with runtime.database.connect() as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM chunks WHERE version_id = ?", (version_id,)
                ).fetchone()[0]
                == 0
            )
        assert not list((tmp_path / "storage").rglob("canonical-document-*.json"))
    finally:
        lease_lost.set()
        thread.join(timeout=12)


@pytest.mark.parametrize("operation", ["create", "upload", "poll", "download"])
@pytest.mark.parametrize("lost_lease", [False, True])
def test_inflight_http_is_cancelled_and_cleaned_up(tmp_path, monkeypatch, operation, lost_lease):
    entered, interrupted, closed = threading.Event(), threading.Event(), threading.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    client_factory = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda: client_factory(transport=httpx.MockTransport(handler))
    )
    transport = MinerUHttpTransport("https://api.example")
    source = tmp_path / "file.png"
    source.write_bytes(b"synthetic")
    errors = []

    def check():
        if interrupted.is_set() and lost_lease:
            raise QueueLeaseError("INGEST_LEASE_LOST")
        return interrupted.is_set()

    def run():
        try:
            with cancellation_scope(check):
                if operation == "create":
                    transport.create_batch(
                        "test-token", "file.png", "id", True, None, "vlm", True, True, 30
                    )
                elif operation == "upload":
                    transport.put_signed("https://upload.example/signed", source, 30)
                elif operation == "poll":
                    transport.batch_status("test-token", "batch", 30)
                else:
                    transport.download_zip("https://download.example/result.zip", 30)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert entered.wait(timeout=3)
        interrupted.set()
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert closed.is_set(), "cancel must await request cleanup"
        assert len(errors) == 1
        assert isinstance(errors[0], QueueLeaseError if lost_lease else IngestionCancelled)
        assert len(calls) == 1
        if operation in {"upload", "download"}:
            assert "authorization" not in calls[0].headers
    finally:
        interrupted.set()
        thread.join(timeout=3)


def test_mineru_poll_wait_can_be_cancelled_without_another_poll_or_result_write(tmp_path):
    source, digest = _source(tmp_path)
    checkpoint = JsonCheckpointStore(tmp_path / "mineru.json")
    pool = _pool()
    transport = _MinerUTransport(pending_polls=10)
    sleeping, cancelled = threading.Event(), threading.Event()
    errors = []

    def sleep(seconds):
        sleeping.set()
        time.sleep(seconds)

    runner = MinerUExecutionRunner(
        pool,
        transport,
        checkpoint,
        _result_store(tmp_path),
        external_call_approved=False,
        poll_interval_seconds=30,
        sleeper=sleep,
    )

    def run():
        try:
            with cancellation_scope(cancelled.is_set):
                runner.run_file(source, "anonymous-cancel", digest)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert sleeping.wait(timeout=3)
        cancelled.set()
        thread.join(timeout=3)
        assert not thread.is_alive() and len(errors) == 1
        assert isinstance(errors[0], IngestionCancelled)
        assert [call[0] for call in transport.calls] == [
            "create_batch",
            "put_signed",
            "batch_status",
        ]
        saved = checkpoint.get("mineru", "anonymous-cancel")
        assert saved["state"] == "SUBMITTED" and saved["batch_id"] == "batch-opaque"
        assert saved["interruption"] == "CANCEL_REQUESTED"
        assert not list((tmp_path / "local-artifacts").rglob("*.zip"))
        # Cancellation releases the token and does not classify it as an upstream failure.
        lease = pool.acquire_slot(int(saved["token_slot"]))
        lease.release()
    finally:
        cancelled.set()
        thread.join(timeout=3)


def test_create_interruption_preserves_unknown_outcome_and_never_resubmits(tmp_path):
    source, digest = _source(tmp_path)
    checkpoints = JsonCheckpointStore(tmp_path / "mineru.json")

    class Transport(_MinerUTransport):
        def create_batch(self, *args):
            self.calls.append(("create_batch", "NO_AUTH"))
            raise IngestionCancelled("INGEST_CANCELLED")

    transport = Transport()
    runner = MinerUExecutionRunner(
        _pool(), transport, checkpoints, _result_store(tmp_path), external_call_approved=False
    )
    with pytest.raises(IngestionCancelled):
        runner.run_file(source, "cancel-unknown", digest)
    saved = checkpoints.get("mineru", "cancel-unknown")
    assert saved["state"] == "UNKNOWN_OUTCOME" and saved["operation"] == "CREATE_BATCH"
    assert saved["interruption"] == "CANCEL_REQUESTED"
    with pytest.raises(ProviderExecutionError, match="MANUAL_RECONCILIATION_REQUIRED"):
        runner.run_file(source, "cancel-unknown", digest)
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "case,expected",
    [
        ("ok", None),
        ("private_redirect", "MINERU_RESULT_URL_FORBIDDEN"),
        ("loop", "MINERU_RESULT_REDIRECT_LIMIT"),
        ("too_large", "MINERU_RESULT_DOWNLOAD_TOO_LARGE"),
        ("invalid_length", "MINERU_RESULT_CONTENT_LENGTH_INVALID"),
        ("server_error", "MINERU_RESULT_DOWNLOAD_FAILED"),
    ],
)
def test_cancellable_download_retains_url_and_size_guards(monkeypatch, case, expected):
    calls = []

    async def handler(request):
        calls.append(request)
        assert "authorization" not in request.headers
        if case == "private_redirect":
            return httpx.Response(302, headers={"Location": "https://127.0.0.1/internal"})
        if case == "loop":
            return httpx.Response(302, headers={"Location": "/again"})
        if case == "server_error":
            return httpx.Response(503)
        if case == "invalid_length":
            return httpx.Response(200, headers={"Content-Length": "unknown"}, content=b"ok")
        return httpx.Response(200, content=b"large" if case == "too_large" else b"ok")

    factory = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda: factory(transport=httpx.MockTransport(handler))
    )
    transport = MinerUHttpTransport("https://api.example", max_download_bytes=3)
    with cancellation_scope(lambda: False):
        if expected:
            with pytest.raises(ProviderExecutionError, match=expected):
                transport.download_zip("https://download.example/result.zip", 5)
        else:
            assert transport.download_zip("https://download.example/result.zip", 5) == b"ok"
    if case == "private_redirect":
        assert len(calls) == 1


def test_cancellable_signed_upload_retains_length_and_body(tmp_path, monkeypatch):
    source = tmp_path / "upload.png"
    source.write_bytes(b"synthetic image")

    async def handler(request):
        assert request.method == "PUT" and await request.aread() == b"synthetic image"
        assert request.headers["content-length"] == str(source.stat().st_size)
        assert "authorization" not in request.headers
        assert "transfer-encoding" not in request.headers
        return httpx.Response(200)

    factory = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda: factory(transport=httpx.MockTransport(handler))
    )
    with cancellation_scope(lambda: False):
        MinerUHttpTransport("https://api.example").put_signed(
            "https://upload.example/signed", source, 5
        )
