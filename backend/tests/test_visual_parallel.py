from __future__ import annotations

import json
import threading
import time
from dataclasses import replace

import pytest
from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.visual_http import VisualAnalyzer
from ragkb.application.acceptance_budget import acceptance_budget, reserve_call
from ragkb.application.cancellation import cancellation_scope, check_cancelled
from ragkb.application.deadlines import remaining_timeout, request_deadline
from ragkb.application.qa_performance import performance_report, performance_scope
from ragkb.application.visual_scheduler import run_visual_checks
from ragkb.domain.errors import IngestionCancelled, ProviderTimeout
from ragkb.infrastructure.model_account import operation
from ragkb.infrastructure.visual_assets import VisualAssetStore
from ragkb.infrastructure.visual_evidence import VisualEvidenceEnricher
from test_visual_correctness import _evidence
from test_visual_pipeline import EXTRACTION, PASS, png, settings


class PairedTransport:
    real_network = False

    def __init__(self, parallel, rejected=None):
        self.barrier = threading.Barrier(2) if parallel else None
        self.rejected = rejected
        self.calls = []
        self.counts = {}
        self.lock = threading.Lock()

    def post_json(self, url, *, headers, payload, timeout):
        version, asset, role = operation.get()
        with self.lock:
            index = self.counts.get(asset, 0)
            self.counts[asset] = index + 1
            self.calls.append((version, asset, index, role))
        if index % 2 == 0:
            if self.barrier:
                self.barrier.wait(timeout=3)
            reply = {"status": "supported", "text": f"Fact for {asset}", "uncertainties": []}
        else:
            reply = PASS if asset != self.rejected else {**PASS, "complete": False}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(reply)}}]}


def setup_images(tmp_path, *, parallel=2, images=2, rejected=False, max_images=4):
    store = VisualAssetStore(LocalFileStorage(tmp_path))
    assets = [store.save_image("v", png(), {"page": i + 1}) for i in range(images)]
    for asset in assets:
        asset.update(status="verified", section_path="Test", extraction=EXTRACTION)
    store.write_manifest("v", assets)
    transport = PairedTransport(parallel > 1, assets[-1]["id"] if rejected else None)
    analyzer = VisualAnalyzer(
        settings(ocr_max_concurrency=2, ocr_query_parallelism=parallel), transport
    )
    owner = VisualEvidenceEnricher(store, analyzer, max_images)
    evidence = tuple(
        replace(_evidence(a), chunk_id=f"chunk-{i}", evidence_id=f"E{i + 1}")
        for i, a in enumerate(assets)
    )
    return owner, transport, assets, evidence


def test_parallel_images_preserve_source_identity_and_all_independent_checks(tmp_path):
    owner, transport, assets, evidence = setup_images(tmp_path)
    with performance_scope():
        result = owner("Question?", evidence)
        events = performance_report()["events"]
    assert len(result) == 2 and len(transport.calls) == 4
    for i, item in enumerate(result):
        assert item.chunk_id == evidence[i].chunk_id
        assert item.display_text == f"Fact for {assets[i]['id']}"
        assert item.locator["visual_asset_ids"] == [assets[i]["id"]]
    assert all([c[2] for c in transport.calls if c[1] == a["id"]] == [0, 1] for a in assets)
    assert any(e.get("workers") == 2 for e in events)
    assert sum(e.get("name") == "evidence.visual_query" for e in events) == 2
    # Each exact supported result may be reused even when no image budget remains.
    session = owner.session("Question?")
    session.attempted = owner.max_images
    assert session(evidence) == result and len(transport.calls) == 4


def test_serial_and_parallel_have_identical_evidence_and_rejection_semantics(tmp_path):
    parallel, pt, _, pe = setup_images(tmp_path / "parallel", rejected=True)
    serial, st, _, se = setup_images(tmp_path / "serial", parallel=1, rejected=True)
    p, s = parallel.session("Question?"), serial.session("Question?")
    assert p(pe) == s(se)
    assert p.warnings == s.warnings == ["VISUAL_EVIDENCE_EXCLUDED:verification_failed"]
    assert len(pt.calls) == len(st.calls) == 4
    # A failed image is never cached as trusted, even when another image passed.
    pt.barrier = None
    parallel("Question?", pe)
    assert len(pt.calls) == 6


def test_budget_selection_and_deferred_images_are_deterministic(tmp_path):
    owner, transport, assets, evidence = setup_images(tmp_path, images=3, max_images=2)
    transport.barrier = None
    session = owner.session("Question?")
    result = session(evidence, reserve_images=1)
    assert len(result) == 1 and session.attempted == 1 and len(transport.calls) == 2
    assert session.deferred == list(evidence[1:])
    session(tuple(session.deferred))
    assert session.attempted == 2 and len(transport.calls) == 4
    assert session.checked["v", assets[-1]["id"]].status == "budget_exceeded"


def test_unpublished_or_unauthorized_images_never_enter_parallel_work(tmp_path):
    owner, transport, _, evidence = setup_images(tmp_path)
    denied = (replace(evidence[0], authorized=False), replace(evidence[1], current_version=False))
    assert owner("Question?", denied) == denied
    assert not transport.calls


def test_scheduler_preserves_order_context_deadline_and_budget():
    barrier = threading.Barrier(2)
    later_done = threading.Event()
    calls = []
    lock = threading.Lock()

    def reserve():
        with lock:
            calls.append(1)

    def run(index):
        assert 0 < remaining_timeout(100) <= 10
        reserve_call.get()()
        barrier.wait(timeout=3)
        if index == 0:
            assert later_done.wait(timeout=3)
        else:
            later_done.set()
        return index

    with request_deadline(10), acceptance_budget(reserve, lambda _: None):
        assert run_visual_checks([0, 1], run, workers=2) == (0, 1)
    assert len(calls) == 2


def test_fatal_image_failure_cancels_siblings_and_never_starts_later_images():
    barrier = threading.Barrier(2)
    stopped = threading.Event()
    started = []

    def run(index):
        started.append(index)
        barrier.wait(timeout=3)
        if index == 0:
            raise ProviderTimeout("image-timeout")
        try:
            for _ in range(1000):
                check_cancelled()
                time.sleep(0.001)
            pytest.fail("Sibling cancellation was not inherited")
        finally:
            stopped.set()

    with pytest.raises(ProviderTimeout):
        run_visual_checks([0, 1, 2, 3], run, workers=2)
    assert sorted(started) == [0, 1] and stopped.is_set()


def test_caller_cancellation_is_inherited_and_workers_are_joined():
    cancel = threading.Event()
    barrier = threading.Barrier(2)
    stopped = []

    def run(index):
        try:
            barrier.wait(timeout=3)
            cancel.set()
            check_cancelled()
        finally:
            stopped.append(index)

    with cancellation_scope(cancel.is_set), pytest.raises(IngestionCancelled):
        run_visual_checks([0, 1, 2], run, workers=2)
    assert sorted(stopped) == [0, 1]


@pytest.mark.parametrize("cap", ["ocr_max_concurrency", "model_account_max_concurrency"])
def test_parallelism_respects_existing_provider_and_account_caps(tmp_path, cap):
    owner, transport, _, evidence = setup_images(tmp_path)
    owner.analyzer.settings = owner.analyzer.settings.model_copy(update={cap: 1})
    transport.barrier = None
    with performance_scope():
        owner("Question?", evidence)
        events = performance_report()["events"]
    assert any(e.get("workers") == 1 for e in events)
