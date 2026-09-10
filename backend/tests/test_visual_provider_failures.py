"""An unavailable image provider must not erase an independently parsed PDF body."""

import json

import httpx
import pytest
from pydantic import SecretStr
from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.visual_http import VisualAnalyzer
from ragkb.document_processing.mineru_parser import MinerUProductionParser
from ragkb.document_processing.visual_parser import VisualDocumentParser
from ragkb.domain.documents import NodeType
from ragkb.domain.errors import (
    IngestionCancelled,
    InvalidProviderResponse,
    ProviderRateLimited,
    ProviderTimeout,
)
from ragkb.domain.validation import DocumentQualityReport, QualityDisposition
from ragkb.infrastructure.visual_assets import VisualAssetStore
from test_mineru_production_parser import _Runner
from test_pdf_layout_routing import LayoutStore, decorated_pdf
from test_visual_pipeline import EXTRACTION, Transport, png, settings


def rejected_request():
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    response = httpx.Response(
        400, request=request, json={"error": {"message": "private-provider-response"}}
    )
    error = InvalidProviderResponse("MODEL_PROVIDER_HTTP_ERROR")
    error.__cause__ = httpx.HTTPStatusError(
        "private-http-detail", request=request, response=response
    )
    return error


def independent_settings():
    return settings(
        ocr_verify_enabled=True,
        ocr_verify_model="independent-test-model",
        ocr_verify_base_url="https://verify.example/v1",
        ocr_verify_api_key=SecretStr("private-verifier-secret"),
        ocr_local_check_enabled=False,
    )


@pytest.mark.parametrize("during_verification", [False, True])
@pytest.mark.parametrize("failure", ["http_400", "rate_limit", "timeout"])
def test_provider_failure_keeps_candidate_without_repair_loop(during_verification, failure):
    error = {
        "http_400": rejected_request,
        "rate_limit": lambda: ProviderRateLimited("MODEL_PROVIDER_RATE_LIMITED"),
        "timeout": lambda: ProviderTimeout("MODEL_PROVIDER_TIMEOUT"),
    }[failure]()
    transport = Transport(*([EXTRACTION] if during_verification else []), error)
    result = VisualAnalyzer(independent_settings(), transport).analyze(png())
    assert result["status"] == "needs_review"
    assert result["error_code"] == error.code
    assert len(transport.calls) == (2 if during_verification else 1)
    assert bool(result["extraction"]) is during_verification
    if during_verification:
        assert result["extraction"]["title"] == EXTRACTION["title"]
        assert result["audit"][0]["stage"] == "extract"
    diagnostic = result["provider_failure"]
    assert diagnostic["phase"] == ("verifying" if during_verification else "extracting")
    assert diagnostic["retryable"] is (failure != "http_400")
    if failure == "http_400":
        assert diagnostic["http_status"] == 400
    assert "private-" not in json.dumps(result)


def test_pdf_body_is_preserved_but_failed_visual_evidence_cannot_be_published(tmp_path):
    source = decorated_pdf(tmp_path / "body-and-picture.pdf")
    config = independent_settings()
    assets = VisualAssetStore(LocalFileStorage(tmp_path / "store"))
    transport = Transport(EXTRACTION, rejected_request())
    parser = VisualDocumentParser(
        MinerUProductionParser(_Runner(), LayoutStore("image"), source_format="pdf", is_ocr=True),
        "pdf",
        VisualAnalyzer(config, transport),
        assets,
        config,
    )
    document = parser.parse(source, "version")
    assert [n.node_type for n in document.nodes] == [
        NodeType.HEADING,
        NodeType.PARAGRAPH,
        NodeType.IMAGE,
    ]
    assert document.nodes[1].display_text == "Warranty is three years."
    assert "OLD_FIGURE_PLACEHOLDER" not in document.nodes[2].display_text
    assert EXTRACTION["transcription"] not in document.nodes[2].display_text
    assert assets.ledger.get("version", "version")["stage"] == "completed"
    saved = assets.list_assets("version")[0]
    assert saved["status"] == saved["stage"] == "needs_review"
    assert saved["extraction"]["title"] == EXTRACTION["title"]
    assert saved["provider_failure"]["http_status"] == 400
    quality = DocumentQualityReport.from_document(document)
    assert quality.node_count == 3
    assert quality.disposition is QualityDisposition.BLOCKED_REAL_VALIDATION
    assert quality.issue_codes == ("VISUAL_REVIEW_REQUIRED:" + saved["id"],)


@pytest.mark.parametrize(
    "error", [RuntimeError("implementation-bug"), IngestionCancelled("CANCELLED")]
)
def test_unexpected_errors_and_cancellation_still_propagate(error):
    with pytest.raises(type(error), match=str(error)):
        VisualAnalyzer(settings(), Transport(error)).analyze(png())


def test_provider_table_survives_supplementary_visual_rejection():
    from ragkb.document_processing.visual_parser import _insert_visual_nodes
    from ragkb.domain.documents import CanonicalNode, SourceLocator

    position = SourceLocator(page=3, bbox=(10, 20, 200, 100))
    table = CanonicalNode(
        "table",
        None,
        NodeType.TABLE,
        "<table><tr><td>华北</td><td>600</td></tr></table>",
        "<table><tr><td>华北</td><td>600</td></tr></table>",
        position,
    )
    rejected = CanonicalNode(
        "image",
        None,
        NodeType.IMAGE,
        "图片尚未通过原图核对，不能用于问答。",
        "图片尚未通过原图核对，不能用于问答。",
        position,
        {"visual_asset_ids": ["crop"], "visual_status": "needs_review"},
    )
    nodes = _insert_visual_nodes([table], [rejected], preserve_tables=True)
    assert nodes == [table, rejected]
    assert not nodes[0].metadata.get("visual_asset_ids")
    assert nodes[1].metadata["visual_asset_ids"] == ["crop"]
