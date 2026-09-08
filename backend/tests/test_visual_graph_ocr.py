from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from ragkb.document_processing import local_visual_check as local
from ragkb.domain.visual_graph import Graph, Node
from ragkb.domain.visuals import VisualExtraction


def extraction(*labels):
    return VisualExtraction(
        kind="diagram",
        title="",
        description="",
        transcription="",
        tables=[],
        uncertainties=[],
        graphs=[
            Graph(
                direction="LR",
                groups=[],
                edges=[],
                uncertainties=[],
                nodes=[
                    Node(id=f"n{i}", label=label, shape="rectangle", group=None)
                    for i, label in enumerate(labels)
                ],
            )
        ],
    )


def reading(*texts):
    return {
        "engine": "fixture",
        "regions": [
            {
                "id": f"r{i}",
                "text": text,
                "score": 0.99,
                "bbox": [0.1, 0.1 + i * 0.3, 0.9, 0.2 + i * 0.3],
            }
            for i, text in enumerate(texts)
        ],
    }


def test_correct_english_numeric_word_boundaries_and_swapped_ownership():
    value = extraction("Product A 220V", "Product B 24V")
    check = local.check_extraction(value, reading("Product A 220V", "Product B 24V"))
    assert check["status"] == "consistent"
    assert [t["region_ids"] for t in check["targets"]] == [["r0"], ["r1"]]
    wrong = local.check_extraction(
        extraction("Product A: 220V", "Product B: 24V"),
        reading("Product A: 24V", "Product B: 220V"),
    )
    assert wrong["status"] == "disagreement"
    assert wrong["issues"] and all(not t["region_ids"] for t in wrong["targets"])


def test_repeated_numeric_labels_need_instance_binding():
    value = extraction("Product A: 220V", "Product A: 220V")
    assert (
        local.check_extraction(value, reading("Product A: 220V", "Product A: 220V"))["status"]
        == "inconclusive"
    )
    assert local.check_extraction(extraction("220V"), reading("220V"))["status"] == "inconclusive"


def test_confirmed_region_binds_split_words_and_numeric_ownership():
    value = extraction("Product A 220V")
    value.graphs[0].nodes[0] = (
        value.graphs[0]
        .nodes[0]
        .model_copy(update={"bbox": [0.0, 0.0, 1.0, 0.7], "bbox_basis": "human"})
    )
    result = local.check_extraction(value, reading("Product A", "220V"))
    assert result["status"] == "consistent"
    assert result["targets"][0]["region_ids"] == ["r0", "r1"]
    wrong = local.check_extraction(value, reading("Product A", "24V", "220V"))
    assert wrong["status"] == "disagreement"


def test_pool_uses_two_exclusive_sessions_and_reuses_them(monkeypatch):
    pool = local.OCRPool()
    monkeypatch.setattr(local, "_make_engine", object)
    barrier = threading.Barrier(2)

    def work():
        with pool.borrow(2) as engine:
            barrier.wait(timeout=3)
            return id(engine)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(work), executor.submit(work)
        assert first.result(timeout=5) != second.result(timeout=5)
    assert pool.created == 2 and pool.active == 0
    with pool.borrow(2):
        assert pool.created == 2


def test_pool_initialization_failure_releases_capacity(monkeypatch):
    pool = local.OCRPool()

    def failure():
        raise RuntimeError("fixture")

    monkeypatch.setattr(local, "_make_engine", failure)
    with pytest.raises(RuntimeError, match="fixture"), pool.borrow(1):
        pass
    assert pool.created == pool.active == 0
    monkeypatch.setattr(local, "_make_engine", object)
    with pool.borrow(1) as engine:
        assert engine is not None and pool.active == 1
