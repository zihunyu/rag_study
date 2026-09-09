"""Every MinerU route must preserve layout and enrich only provider figure crops."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from pydantic import SecretStr
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.visual_http import VisualAnalyzer
from ragkb.application.provider_runners import MinerUExecutionRunner
from ragkb.contracts.ports import ParsingDeferred
from ragkb.document_processing.mineru_parser import MinerUProductionParser
from ragkb.document_processing.parsers import ParserRouter
from ragkb.document_processing.visual_coverage import inventory
from ragkb.document_processing.visual_parser import VisualDocumentParser
from ragkb.document_processing.visual_sources import mineru_images
from ragkb.domain.documents import NodeType
from ragkb.infrastructure.provider_results import LocalProviderResultStore
from ragkb.infrastructure.visual_assets import VisualAssetStore
from ragkb.runtime_profiles.production import ProductionRuntimeFactory
from test_mineru_production_parser import _Runner, _Store
from test_visual_pipeline import EXTRACTION, PASS, Transport, png, settings


def decorated_pdf(path: Path) -> Path:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=400)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 18 Tf 20 350 Td (Readable PDF title) Tj ET 20 345 180 1 re f")
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.write(path)
    return path


@pytest.mark.parametrize("kind", ["pdf", "pdf_scanned"])
def test_production_pdf_calls_mineru_even_with_readable_text(tmp_path, monkeypatch, kind):
    source = decorated_pdf(tmp_path / "text.pdf")
    assert "Readable PDF title" in PdfReader(source).pages[0].extract_text()
    calls = []

    def run_file(self, source, anonymous_id, expected_sha256, *, is_ocr):
        calls.append(source)
        return _Runner().run_file(source, anonymous_id, expected_sha256, is_ocr=is_ocr)

    monkeypatch.setattr(MinerUExecutionRunner, "run_file", run_file)
    monkeypatch.setattr(LocalProviderResultStore, "read_mineru_nodes", _Store.read_mineru_nodes)
    config = settings(
        app_env="production",
        rag_runtime_profile="production",
        mineru_tokens=(SecretStr("test-token"),),
        local_storage_artifacts_dir=tmp_path / "artifacts",
    )
    router = ProductionRuntimeFactory().build_parser(
        config, tmp_path, LocalFileStorage(tmp_path / "store")
    )
    document = router.parse(kind, source, "version-1")
    assert calls == [source]
    assert document.parser_revision == MinerUProductionParser.revision
    assert [n.node_type for n in document.nodes] == [NodeType.HEADING, NodeType.PARAGRAPH]


class LayoutStore(_Store):
    def __init__(self, figure_type=None):
        self.figure_type = figure_type

    def read_mineru_nodes(self, artifact_id):
        nodes = super().read_mineru_nodes(artifact_id)
        if self.figure_type:
            nodes.append(
                {
                    "node_id": "figure",
                    "type": self.figure_type,
                    "display_text": "OLD_FIGURE_PLACEHOLDER",
                    "locator": {"page": 1, "bbox": [0, 100, 100, 200]},
                }
            )
        return nodes

    def read_mineru_zip(self, artifact_id):
        assert artifact_id == "artifact-1"
        contents = [
            # A crop supplied alongside text/equations is not an illustration.
            {"type": "text", "page_idx": 0, "img_path": "images/decoration.png"},
            {"type": "equation", "page_idx": 0, "img_path": "images/equation.png"},
        ]
        if self.figure_type:
            contents.append(
                {
                    "type": self.figure_type,
                    "page_idx": 0,
                    "bbox": [0, 100, 100, 200],
                    "img_path": "images/figure.png",
                }
            )
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("result/content_list.json", json.dumps(contents))
            archive.writestr("result/images/figure.png", png())
        return output.getvalue()


def mineru_source(tmp_path, kind):
    if kind in {"pdf", "pdf_scanned"}:
        source = decorated_pdf(tmp_path / "decorated.pdf")
        assert inventory(source, "pdf")["render_pages"] == [1]
        return source
    source = tmp_path / ("image.png" if kind == "image" else f"legacy.{kind}")
    # The provider response is replayed; native parsers must never read these files.
    source.write_bytes(png() if kind == "image" else b"synthetic-legacy-office")
    return source


@pytest.mark.parametrize("kind", ["pdf", "pdf_scanned", "doc", "ppt", "image"])
@pytest.mark.parametrize("figure_type", [None, "image", "chart", "table"])
def test_mineru_layout_preserves_body_and_enriches_only_provider_figures(
    tmp_path, monkeypatch, kind, figure_type
):
    source = mineru_source(tmp_path, kind)
    provider_calls = []

    def run_file(self, source, anonymous_id, expected_sha256, *, is_ocr):
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected_sha256
        assert anonymous_id
        provider_calls.append((source, is_ocr))
        return {"artifact_id": "artifact-1"}

    results = LayoutStore(figure_type)
    monkeypatch.setattr(MinerUExecutionRunner, "run_file", run_file)
    monkeypatch.setattr(
        LocalProviderResultStore,
        "read_mineru_nodes",
        lambda self, identity: results.read_mineru_nodes(identity),
    )
    monkeypatch.setattr(
        LocalProviderResultStore,
        "read_mineru_zip",
        lambda self, identity: results.read_mineru_zip(identity),
    )

    def never(*args, **kwargs):
        pytest.fail("MinerU layout must not fall back to native images or whole-page rendering")

    monkeypatch.setattr("ragkb.document_processing.visual_parser.inventory", never)
    monkeypatch.setattr("ragkb.document_processing.visual_parser.native_images", never)
    monkeypatch.setattr("ragkb.document_processing.visual_parser.render_fallback", never)
    config = settings(
        app_env="production",
        rag_runtime_profile="production",
        mineru_tokens=(SecretStr("test-token"),),
        local_storage_artifacts_dir=tmp_path / "artifacts",
        ocr_render_fallback_enabled=True,
        ocr_local_check_enabled=False,
    )
    transport = Transport(EXTRACTION, PASS) if figure_type else Transport()
    storage = LocalFileStorage(tmp_path / "store")
    assets = VisualAssetStore(storage)
    base = ProductionRuntimeFactory().build_parser(config, tmp_path, storage).route(kind)
    document = VisualDocumentParser(
        base, kind, VisualAnalyzer(config, transport), assets, config
    ).parse(source, "version-1")
    assert provider_calls == [(source, kind not in {"doc", "ppt"})]
    assert document.source_format == ("pdf" if kind == "pdf_scanned" else kind)
    assert document.nodes[0].node_type is NodeType.HEADING
    assert document.nodes[0].display_text == "Policy"
    assert document.nodes[1].node_type is NodeType.PARAGRAPH
    assert document.nodes[1].display_text == "Warranty is three years."
    assert not document.quality_issues
    assert len(transport.calls) == (2 if figure_type else 0)
    assert len(document.media_refs) == (1 if figure_type else 0)
    assert all("OLD_FIGURE_PLACEHOLDER" not in n.display_text for n in document.nodes)
    if figure_type:
        assert document.nodes[2].locator.bbox == (0.0, 100.0, 100.0, 200.0)
        assert document.nodes[2].metadata["section_path"] == "Policy"
        assert document.media_refs[0]["locator"]["part"] == "images/figure.png"
    coverage = assets.ledger.get("version", "version-1")["coverage"]
    assert coverage["layout_parser"] == MinerUProductionParser.revision
    assert coverage["needs_render"] is False


@pytest.mark.parametrize("figure_type", ["image", "chart", "table"])
def test_mineru_visual_sources_exclude_text_and_equation_crops(figure_type):
    images = mineru_images(LayoutStore(figure_type).read_mineru_zip("artifact-1"), 1_000_000)
    assert len(images) == 1
    assert images[0].locator.part == "images/figure.png"


@pytest.mark.parametrize("kind", ["pdf", "pdf_scanned", "doc", "ppt", "image"])
def test_mineru_provider_failure_is_not_silently_replaced_with_image_ocr(tmp_path, kind):
    class FailingRunner:
        def run_file(self, *args, **kwargs):
            raise ParsingDeferred("OCR_REQUIRED", "provider unavailable")

    source = mineru_source(tmp_path, kind)
    transport = Transport()
    config = settings(ocr_render_fallback_enabled=True)
    base = MinerUProductionParser(FailingRunner(), LayoutStore(), source_format=kind, is_ocr=True)
    parser = VisualDocumentParser(
        base,
        kind,
        VisualAnalyzer(config, transport),
        VisualAssetStore(LocalFileStorage(tmp_path / "store")),
        config,
    )
    with pytest.raises(ParsingDeferred, match="provider unavailable"):
        ParserRouter({kind: parser}).parse(kind, source, "v")
    assert transport.calls == []


def test_mineru_text_levels_survive_result_storage_as_real_headings(tmp_path):
    from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore
    from test_provider_runners import _MinerUTransport, _pool

    source = decorated_pdf(tmp_path / "text.pdf")
    store = LocalProviderResultStore(tmp_path / "artifacts")
    runner = MinerUExecutionRunner(
        _pool(),
        _MinerUTransport(),
        JsonCheckpointStore(tmp_path / "checkpoints.json"),
        store,
        external_call_approved=False,
    )
    levels = [1, 2, None, 0, True, "2", 7]
    raw = [
        {
            "type": "text",
            "text": f"Block {i}",
            "text_level": level,
            "page_idx": 0,
            "bbox": [0, i * 20, 100, i * 20 + 15],
        }
        for i, level in enumerate(levels)
    ]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("content_list.json", json.dumps(raw))
    payload = output.getvalue()
    nodes, _, digest = runner.validate_result_zip(payload, "levels")
    evidence = store.persist_mineru_result("levels", digest, payload, nodes)

    class SavedRunner:
        def run_file(self, *args, **kwargs):
            return evidence

    document = MinerUProductionParser(SavedRunner(), store, source_format="pdf", is_ocr=True).parse(
        source, "v"
    )
    assert [n.node_type for n in document.nodes] == [
        NodeType.HEADING,
        NodeType.HEADING,
        *([NodeType.PARAGRAPH] * 5),
    ]
    assert [n.metadata["heading_level"] for n in document.nodes[:2]] == [1, 2]
