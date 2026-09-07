from __future__ import annotations

import copy
import io
import json
from dataclasses import replace

import pytest
from docx import Document
from PIL import Image
from pydantic import SecretStr, ValidationError
from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.visual_http import VisualAnalyzer
from ragkb.config import EnvSettings
from ragkb.config.env import SECRET_KEYS, load_env
from ragkb.document_processing.parsers import DOCXParser, ImageParserRoute
from ragkb.document_processing.visual_parser import VisualDocumentParser
from ragkb.domain.documents import SourceLocator
from ragkb.domain.rag import Evidence
from ragkb.domain.validation import DocumentQualityReport, QualityDisposition
from ragkb.domain.visual_graph import Graph, to_mermaid
from ragkb.domain.visuals import VisualExtraction, VisualTable
from ragkb.infrastructure.visual_assets import VisualAssetStore
from ragkb.infrastructure.visual_evidence import VisualEvidenceEnricher

GRAPH = {
    "direction": "LR",
    "groups": [{"id": "cluster", "label": "服务组", "parent": None, "direction": "TB"}],
    "nodes": [
        {"id": "a", "label": "Gateway", "group": None, "shape": "rectangle"},
        {"id": "b", "label": "订单服务", "group": "cluster", "shape": "rounded"},
    ],
    "edges": [
        {"source": "a", "target": "b", "label": "请求", "direction": "both", "style": "dashed"}
    ],
    "uncertainties": [],
}
EXTRACTION = {
    "kind": "diagram",
    "title": "请求链路",
    "transcription": "Gateway 订单服务 请求",
    "description": "网关与订单服务双向连接。",
    "graphs": [GRAPH],
    "tables": [],
    "uncertainties": [],
}
PASS = {
    "kind_correct": True,
    "text_correct": True,
    "structure_correct": True,
    "complete": True,
    "issues": [],
}


class Transport:
    real_network = False

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post_json(self, url, *, headers, payload, timeout):
        self.calls.append((url, headers, payload))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return {
            "model": "test-vision",
            "id": str(len(self.calls)),
            "usage": {"total_tokens": 50},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
        }


def settings(**kwargs):
    kwargs.setdefault("ocr_max_concurrency", 1)
    kwargs.setdefault("ocr_render_fallback_enabled", False)
    kwargs.setdefault("ocr_cross_version_cache_enabled", False)
    return EnvSettings(
        ocr_enabled=True,
        ocr_base_url="https://ocr.example/v1",
        ocr_api_key=SecretStr("synthetic-test-secret"),
        ocr_model="test-vision",
        **kwargs,
    )


def png():
    out = io.BytesIO()
    Image.new("RGB", (180, 100), "white").save(out, format="PNG")
    return out.getvalue()


def test_graph_compiler_preserves_directions_groups_and_labels():
    graph = Graph.model_validate(GRAPH)
    text = to_mermaid(graph)
    assert "<-.->" in text and "subgraph g1" in text and "订单服务" in text
    graph.nodes[0].label = 'A["] <script> & `click`'
    escaped = to_mermaid(graph)
    assert "<script>" not in escaped and "#60;script#62;" in escaped
    assert to_mermaid(graph) == escaped


@pytest.mark.parametrize("change", ["missing_end", "duplicate", "cycle"])
def test_graph_invalid_references_are_rejected(change):
    graph = copy.deepcopy(GRAPH)
    if change == "missing_end":
        graph["edges"][0]["target"] = "missing"
    elif change == "duplicate":
        graph["nodes"][1]["id"] = "a"
    else:
        graph["groups"][0]["parent"] = "cluster"
    with pytest.raises(ValueError):
        to_mermaid(Graph.model_validate(graph))


def test_table_merge_validation_and_safe_html():
    table = {
        "title": "参数",
        "rows": 2,
        "columns": 2,
        "cells": [
            {
                "row": 0,
                "column": 0,
                "rowspan": 1,
                "colspan": 2,
                "text": "<img src=x onerror=alert(1)>",
            },
            {"row": 1, "column": 0, "rowspan": 1, "colspan": 1, "text": "功率"},
            {"row": 1, "column": 1, "rowspan": 1, "colspan": 1, "text": "420 W"},
        ],
    }
    html = VisualTable.model_validate(table).as_html()
    assert 'colspan="2"' in html and "<img" not in html and "420 W" in html
    table["cells"].pop()
    with pytest.raises(ValidationError):
        VisualTable.model_validate(table)
    table["cells"].append(copy.deepcopy(table["cells"][0]))
    with pytest.raises(ValidationError):
        VisualTable.model_validate(table)


def test_ocr_uses_independent_config_and_a_second_image_verification():
    transport = Transport(EXTRACTION, PASS)
    result = VisualAnalyzer(settings(), transport).analyze(png())
    assert result["status"] == "verified" and len(transport.calls) == 2
    for url, _, payload in transport.calls:
        assert url == "https://ocr.example/v1/chat/completions"
        assert payload["model"] == "test-vision"
        assert any(p["type"] == "image_url" for p in payload["messages"][1]["content"])
    assert "synthetic-test-secret" not in json.dumps(result)


def test_uncertainty_never_passes_even_when_reviewer_says_pass():
    uncertain = copy.deepcopy(EXTRACTION)
    uncertain["graphs"][0]["uncertainties"] = ["右侧箭头看不清"]
    transport = Transport(uncertain, PASS, uncertain, PASS)
    result = VisualAnalyzer(settings(), transport).analyze(png())
    assert result["status"] == "needs_review" and len(transport.calls) == 4


def test_disagreement_triggers_bounded_reextraction():
    transport = Transport(
        EXTRACTION, {**PASS, "structure_correct": False, "issues": ["漏掉箭头"]}, EXTRACTION, PASS
    )
    result = VisualAnalyzer(settings(), transport).analyze(png())
    assert result["status"] == "verified"
    assert "漏掉箭头" in transport.calls[2][2]["messages"][0]["content"]


def test_photo_is_not_forced_into_graph():
    photo = {
        **EXTRACTION,
        "kind": "photo",
        "graphs": [],
        "transcription": "",
        "description": "白色物体。",
    }
    assert VisualAnalyzer(settings(), Transport(photo, PASS)).analyze(png())["status"] == "verified"
    with pytest.raises(ValidationError):
        VisualExtraction.model_validate({**photo, "graphs": [GRAPH]})


def test_docx_image_only_small_image_is_retained_and_locatable(tmp_path):
    image = tmp_path / "small.png"
    image.write_bytes(png())
    assert image.stat().st_size < 10240
    source = tmp_path / "embedded.docx"
    doc = Document()
    doc.add_picture(str(image))
    doc.save(source)
    store = VisualAssetStore(LocalFileStorage(tmp_path / "store"))
    transport = Transport(EXTRACTION, PASS)
    parser = VisualDocumentParser(
        DOCXParser(), "docx", VisualAnalyzer(settings(), transport), store, settings()
    )
    canonical = parser.parse(source, "v1")
    assert len(canonical.media_refs) == 1
    assert canonical.nodes[-1].locator.part.startswith("word/media/")
    assert canonical.nodes[-1].locator.page is None
    assert "双向连接" in canonical.nodes[-1].original_text
    from ragkb.document_processing.chunking import TokenAwareChunker

    chunks = TokenAwareChunker().chunk(canonical, tenant_id="test")
    visual_chunks = [chunk for chunk in chunks.chunks if chunk.metadata.get("visual_asset_ids")]
    assert visual_chunks
    assert all(chunk.locator.part == "word/media/image1.png" for chunk in visual_chunks)
    assert all(chunk.locator.paragraph == 1 for chunk in visual_chunks)
    assert store.read_image("v1", store.list_assets("v1")[0]) == png()
    parser.parse(source, "v1")
    assert len(transport.calls) == 2  # completed verified extraction recovered on retry
    expired = store.list_assets("v1")
    expired[0]["analyzed_at"] = 0
    store.write_manifest("v1", expired)
    transport.responses.extend([EXTRACTION, PASS])
    parser.parse(source, "v1")
    assert len(transport.calls) == 4
    with pytest.raises(FileNotFoundError):
        store.get("another-version", canonical.media_refs[0]["id"])


def test_uncertain_image_blocks_quality_and_does_not_index_claims(tmp_path):
    source = tmp_path / "image.png"
    source.write_bytes(png())
    transport = Transport(EXTRACTION, {**PASS, "text_correct": False, "issues": ["数字模糊"]})
    cfg = settings(ocr_max_repair_attempts=0)
    parser = VisualDocumentParser(
        ImageParserRoute(),
        "image",
        VisualAnalyzer(cfg, transport),
        VisualAssetStore(LocalFileStorage(tmp_path / "store")),
        cfg,
    )
    document = parser.parse(source, "v2")
    assert "Gateway" not in document.nodes[0].original_text
    assert (
        DocumentQualityReport.from_document(document).disposition
        == QualityDisposition.BLOCKED_REAL_VALIDATION
    )


def test_asset_hash_detects_tampering(tmp_path):
    store = VisualAssetStore(LocalFileStorage(tmp_path))
    asset = store.save_image("v", png(), {"part": "x.png"})
    store.write_manifest("v", [asset])
    store.storage.write_bytes("artifacts", asset["storage_key"], b"changed")
    with pytest.raises(ValueError, match="INTEGRITY"):
        store.read_image("v", asset)


def test_query_recheck_uses_only_authorized_linked_images(tmp_path):
    store = VisualAssetStore(LocalFileStorage(tmp_path))
    asset = store.save_image("v", png(), {"part": "x.png"})
    asset["status"] = "verified"
    store.write_manifest("v", [asset])
    transport = Transport(
        {"status": "supported", "text": "Gateway 与订单服务双向连接。", "uncertainties": []}, PASS
    )
    enrich = VisualEvidenceEnricher(store, VisualAnalyzer(settings(), transport), 1)
    evidence = Evidence(
        "E1",
        "c",
        "d",
        "v",
        "请求链路",
        {"part": "x.png", "visual_asset_ids": [asset["id"]]},
        0,
        0,
        0,
        1,
        True,
        True,
    )
    session = enrich.session("请求如何传递？")
    result = session((evidence,))
    assert "双向连接" in result[0].text and result[0].chunk_id == "c"
    assert result[0].document_version_id == "v"
    assert session(result) == result and len(transport.calls) == 2
    enrich("问题", (replace(evidence, authorized=False),))
    assert len(transport.calls) == 2


def test_ocr_secret_is_registered_and_missing_values_do_not_fall_back_to_llm(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "LLM_MODEL=generator\nOCR_ENABLED=true\nOCR_MODEL=vision\nOCR_API_KEY=test-ocr-secret\n",
        encoding="utf-8",
    )
    result = load_env(env_path=path, environ={})
    assert "OCR_API_KEY" in SECRET_KEYS
    assert result.settings.ocr_model == "vision" and result.settings.ocr_base_url == ""
    assert "test-ocr-secret" not in repr(result.settings)


def test_part_locator_does_not_fabricate_page():
    assert SourceLocator(part="word/media/image2.png").to_dict() == {
        "part": "word/media/image2.png"
    }


def test_large_image_keeps_full_view_and_overlapping_details():
    output = io.BytesIO()
    Image.new("RGB", (1900, 1900), "white").save(output, format="PNG")
    views = VisualAnalyzer(settings(), Transport())._images(output.getvalue())
    assert len([v for v in views if v["type"] == "image_url"]) == 5
    assert views[0]["text"].startswith("原图全景")
    assert all("同一原图" in views[i]["text"] for i in (2, 4, 6, 8))


def test_empty_provider_choices_become_reviewable_failure():
    class EmptyChoices(Transport):
        def post_json(self, *args, **kwargs):
            return {"choices": []}

    result = VisualAnalyzer(settings(ocr_max_repair_attempts=0), EmptyChoices()).analyze(png())
    assert result["status"] == "needs_review"
    assert result["extraction"] is None
