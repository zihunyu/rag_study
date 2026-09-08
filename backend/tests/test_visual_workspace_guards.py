"""Regression cases for edits, source highlighting and durable worker/account fencing."""

from __future__ import annotations

import copy
import os
import time
import uuid

import pytest
from ragkb.document_processing.local_visual_check import mentions_region
from ragkb.domain.visuals import VisualExtraction, reviewed_extraction
from ragkb.infrastructure.visual_ledger import VisualLedger
from test_visual_pipeline import EXTRACTION


def test_structural_edit_cannot_keep_old_transcript_and_generated_description():
    original = VisualExtraction.model_validate(EXTRACTION)
    raw = copy.deepcopy(EXTRACTION)
    raw["graphs"][0]["nodes"][1]["label"] = "支付服务"
    raw["graphs"][0]["edges"][0]["direction"] = "forward"
    edited = reviewed_extraction(original, VisualExtraction.model_validate(raw))
    assert "订单服务" not in edited.retrieval_text()
    assert "双向" not in edited.retrieval_text()
    assert "Gateway 指向" in edited.retrieval_text()
    assert "支付服务" in edited.retrieval_text()
    assert edited.graphs[0].groups[0].label in edited.retrieval_text()
    assert original.transcription == EXTRACTION["transcription"]
    assert reviewed_extraction(original, edited) == edited


def test_citation_region_matching_preserves_numeric_boundaries():
    assert mentions_region("额定功率为 323 W。[E1]", "323W")
    assert not mentions_region("额定功率为 323 W。[E1]", "23 W")
    assert not mentions_region("数值为 23.5。", "23")
    assert not mentions_region("额定功率 23 MW。", "23 mW")
    from ragkb.document_processing.local_visual_check import check_extraction

    value = VisualExtraction(
        kind="text",
        title="",
        transcription="23 mW",
        description="",
        graphs=[],
        tables=[],
        uncertainties=[],
    )
    check = check_extraction(
        value, {"engine": "test", "regions": [{"id": "r1", "text": "23 MW", "score": 1.0}]}
    )
    assert check["status"] == "disagreement"


def test_unused_model_client_closes_during_asgi_shutdown():
    import asyncio

    from ragkb.adapters.deadline_http import DeadlineHttpClient

    client = DeadlineHttpClient()

    async def shutdown():
        client.close()
        client.close()

    asyncio.run(shutdown())
    assert client.loop.is_closed() and not client.thread.is_alive()


def test_gold_evaluation_distinguishes_units_and_arrow_direction():
    import json
    from pathlib import Path

    from ragkb.domain.visual_evaluation import evaluate_pair

    cases = json.loads(
        (Path(__file__).parent / "fixtures/visual_evaluation/dataset.json").read_text(
            encoding="utf-8"
        )
    )["cases"]
    diagram = next(c["gold"] for c in cases if c["id"] == "two-way")
    wrong = copy.deepcopy(diagram)
    wrong["graphs"][0]["edges"][0]["direction"] = "forward"
    metrics = evaluate_pair(
        VisualExtraction.model_validate(diagram), VisualExtraction.model_validate(wrong)
    )
    assert metrics["nodes_correct"] == 2
    assert metrics["edges_expected"] == 1 and metrics["edges_correct"] == 0


def test_visual_recheck_receives_actual_section_and_caption_without_requiring_them_in_pixels(
    tmp_path,
):
    from ragkb.adapters.local_storage import LocalFileStorage
    from ragkb.domain.visuals import VisualQueryOutcome
    from ragkb.infrastructure.visual_assets import VisualAssetStore
    from ragkb.infrastructure.visual_evidence import VisualEvidenceEnricher
    from test_visual_correctness import _evidence
    from test_visual_pipeline import png

    store = VisualAssetStore(LocalFileStorage(tmp_path))
    asset = store.save_image("v", png(), {"part": "image.png"})
    asset.update(
        status="verified",
        extraction=EXTRACTION,
        section_path="Product A",
        caption="Figure 6",
        context="Only for A",
    )
    store.write_manifest("v", [asset])

    class Recorder:
        revision = "test"

        def query(self, data, question, prior):
            assert '"section": "Product A"' in question and '"caption": "Figure 6"' in question
            assert "Only for A" in question and "Gateway" in prior
            return VisualQueryOutcome("supported", "Gateway connects to the service.")

    answer = VisualEvidenceEnricher(store, Recorder(), 6)(
        "Summarize six images", (_evidence(asset),)
    )
    assert len(answer) == 1 and "Gateway connects" in answer[0].text


def test_ledger_fences_late_workers_and_cleans_content_without_losing_usage(tmp_path):
    ledger = VisualLedger(tmp_path / "visual.sqlite")
    ledger.put("version", "v1", {"run": "old"})
    ledger.put("version", "v1", {"run": "new"}, expected=1)
    with pytest.raises(ValueError, match="VISUAL_REVISION_CONFLICT"):
        ledger.put("version", "v1", {"run": "old"}, expected=1)
    asset = {"id": "a", "ordinal": 0, "storage_key": "v1/image", "extraction": "private text"}
    with pytest.raises(ValueError, match="VISUAL_EXECUTION_SUPERSEDED"):
        ledger.upsert_asset("v1", asset, run="old")
    ledger.upsert_asset("v1", asset, run="new")
    ledger.put(
        "version_plan", "v2", {"base_version_id": "v1", "edits": {"a": "private"}}, immutable=True
    )
    with pytest.raises(ValueError, match="VISUAL_PLAN_CONFLICT"):
        ledger.put("version_plan", "v2", {"base_version_id": "other"}, immutable=True)
    ledger.cache_put("tenant", "hash", {"_source_version_id": "v1", "text": "private"}, 60)
    ledger.cache_put("tenant", "unrelated", {"_source_version_id": "v3"}, 60)
    # Totals must cover all records, even though the detail panel is bounded to 2000.
    with ledger.connect() as db:
        db.executemany(
            "INSERT INTO visual_usage(version_id,asset_id,role,model,started,elapsed,"
            "outcome,usage,cost,cached) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [("v1", "a", "ocr", "test", 1, 2, "200", '{"prompt_tokens":3}', None, 0)] * 2005,
        )
    report = ledger.usage_report("v1")
    assert len(report["records"]) == 2000 and report["call_count"] == 2005
    assert report["input_tokens"] == 6015 and report["unpriced_calls"] == 2005
    ledger.purge_artifacts(["v1/image"])
    ledger.purge_artifacts(["v1/image"])
    assert not ledger.assets("v1") and not ledger.get("version_plan", "v2")
    assert ledger.cache_get("tenant", "hash") is None
    assert ledger.cache_get("tenant", "unrelated") is not None
    assert ledger.usage_report("v1")["call_count"] == 2005


@pytest.mark.skipif(
    os.environ.get("RAG_RUN_REDIS_ACCOUNT_TEST") != "1",
    reason="explicit opt-in for isolated real Redis reservations",
)
def test_real_redis_shares_concurrency_tokens_and_429_cooldown_across_clients():
    from ragkb.config import load_env
    from ragkb.domain.errors import ProviderRateLimited, ProviderTimeout
    from ragkb.infrastructure.model_account import AccountLimiter

    config = load_env().settings.model_copy(
        update={
            "redis_key_prefix": "rag-visual-acceptance:" + uuid.uuid4().hex + ":",
            "model_account_group": "test-shared-account",
            "model_account_max_concurrency": 1,
            "model_account_tokens_per_minute": 1000,
            "model_account_requests_per_minute": 20,
        }
    )
    first, second = AccountLimiter(config), AccountLimiter(config)
    keys = []
    try:
        with first.reserve("https://example.invalid/v1", {}, {"max_tokens": 100}, 2) as lease:
            keys = lease[0]
            with pytest.raises(ProviderTimeout, match="QUOTA_WAIT"):
                with second.reserve("https://another.invalid/v1", {}, {"max_tokens": 1}, 0.2):
                    pytest.fail("A second client bypassed the shared concurrency limit")
            first.settle(lease, {"total_tokens": 5}, 200)
            assert int(first.redis.hget(keys[2], lease[1])) == 5
        assert first.redis.zcard(keys[0]) == 0
        with second.reserve("https://another.invalid/v1", {}, {"max_tokens": 100}, 2) as lease:
            second.settle(lease, {}, 429, "30")
            cooldown = float(second.redis.get(keys[3]))
            second.settle(lease, {}, 429, "1")
            assert float(second.redis.get(keys[3])) >= cooldown
        with pytest.raises(ProviderTimeout, match="QUOTA_WAIT"):
            with first.reserve("https://example.invalid/v1", {}, {}, 0.2):
                pytest.fail("Account cooldown was bypassed")
        with pytest.raises(ProviderRateLimited, match="EXCEEDS_TOKEN_BUDGET"):
            with first.reserve("https://example.invalid/v1", {}, {"max_tokens": 1001}, 0.2):
                pytest.fail("Oversized reservation was accepted")
        assert cooldown > time.time()
    finally:
        if keys:
            # Only keys returned by our UUID-scoped leases; never scan or flush Redis.
            first.redis.delete(*keys)
        first.redis.close()
        second.redis.close()
