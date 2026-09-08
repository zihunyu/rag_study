from __future__ import annotations

import gzip
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from ragkb.infrastructure.visual_ledger import VisualLedger
from ragkb.infrastructure.worker_heartbeat import worker_status


def test_heartbeat_distinguishes_alive_stale_and_stalled_work(tmp_path):
    ledger = VisualLedger(tmp_path / "visual.sqlite")
    assert worker_status(ledger, now=100)["state"] == "unprobed"
    item = {
        "worker_id": "w",
        "heartbeat_epoch": 98.0,
        "activity_epoch": 1.0,
        "job_id": "j",
        "version_id": "v",
        "phase": "parsing",
        "stopped": False,
    }
    ledger.put("worker_heartbeat", "w:instance", item)
    assert worker_status(ledger, now=100, stall_seconds=60)["state"] == "stalled"
    ledger.upsert_asset("v", {"id": "a", "updated_at": 97.0})
    assert worker_status(ledger, now=100, stall_seconds=60)["state"] == "ready"
    assert worker_status(ledger, now=200, stale_seconds=30)["state"] == "unavailable"
    ledger.put(
        "worker_heartbeat",
        "w:new",
        {
            **item,
            "heartbeat_epoch": 199.0,
            "activity_epoch": 199.0,
            "job_id": "",
            "version_id": "",
            "phase": "idle",
        },
    )
    assert worker_status(ledger, now=200)["state"] == "ready"


def test_accounting_backfill_is_idempotent_and_archives_keep_lifetime_totals(tmp_path):
    ledger = VisualLedger(tmp_path / "visual.sqlite")
    ledger.usage(
        "v1",
        role="ocr",
        model="fake",
        started=1,
        outcome="200",
        cost=0.2,
        usage={"prompt_tokens": 50, "completion_tokens": 5},
    )
    ledger.usage("v2", role="ocr", model="fake", started=time.time(), outcome="cache", cached=True)
    before = ledger.usage_report()
    assert before["call_count"] == 1 and before["cache_hits"] == 1
    assert ledger.usage_report()["input_tokens"] == 50
    archived = ledger.archive_usage(before=2)
    assert archived["archived_count"] == 1
    archive = ledger.path.parent / "visual-usage-archive" / archived["archive"]
    raw = [json.loads(line) for line in gzip.decompress(archive.read_bytes()).decode().splitlines()]
    assert raw[0]["version_id"] == "v1"
    assert ledger.archive_usage(before=2)["archived_count"] == 0
    assert ledger.usage_report("v1")["call_count"] == 1
    assert ledger.usage_report("v1")["records"] == []
    for name in ["call_count", "cache_hits", "input_tokens", "output_tokens", "known_cost_cny"]:
        assert ledger.usage_report()[name] == before[name]
    # Reopening and a new increment cannot count archived rows twice or reset history.
    ledger = VisualLedger(ledger.path)
    ledger.usage(
        "v1",
        role="ocr",
        model="fake",
        started=time.time(),
        outcome="200",
        usage={"prompt_tokens": 7},
    )
    assert ledger.usage_report("v1")["input_tokens"] == 57
    assert ledger.usage_report()["call_count"] == 2


@pytest.mark.skipif(
    os.environ.get("RAG_RUN_REDIS_ACCOUNT_TEST") != "1", reason="isolated Redis opt-in"
)
def test_interactive_requests_precede_queued_ingestion_and_cleanup_waiters():
    from ragkb.config import load_env
    from ragkb.infrastructure.model_account import AccountLimiter, background_requests

    config = load_env().settings.model_copy(
        update={
            "redis_key_prefix": "rag-priority-test:" + uuid.uuid4().hex + ":",
            "model_account_group": "isolated-priority",
            "model_account_max_concurrency": 1,
            "model_account_tokens_per_minute": 100000,
            "model_account_interactive_burst": 3,
        }
    )
    limiter = AccountLimiter(config)
    order = []
    keys = []

    def wait_for_waiter(index):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if limiter.redis.zcard(keys[index]):
                return
            time.sleep(0.01)
        pytest.fail("waiter did not register")

    def call(role):
        if role == "background":
            with background_requests(), limiter.reserve("https://example.invalid", {}, {}, 5):
                order.append(role)
        else:
            with limiter.reserve("https://example.invalid", {}, {}, 5):
                order.append(role)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            with limiter.reserve("https://example.invalid", {}, {}, 5) as lease:
                keys = lease[0]
                background = pool.submit(call, "background")
                wait_for_waiter(5)
                foreground = pool.submit(call, "interactive")
                wait_for_waiter(4)
            foreground.result(timeout=6)
            background.result(timeout=6)
        assert order == ["interactive", "background"]
        assert all(limiter.redis.zcard(key) == 0 for key in keys[4:7])
    finally:
        if keys:
            limiter.redis.delete(*keys)
