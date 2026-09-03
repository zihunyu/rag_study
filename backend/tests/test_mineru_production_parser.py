from __future__ import annotations

import hashlib
from pathlib import Path

from ragkb.document_processing.mineru_parser import MinerUProductionParser
from ragkb.domain.documents import NodeType


class _Runner:
    def run_file(self, source, anonymous_id, expected_sha256, *, is_ocr):
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected_sha256
        assert anonymous_id and is_ocr
        return {"artifact_id": "artifact-1"}


class _Store:
    def read_mineru_nodes(self, artifact_id):
        assert artifact_id == "artifact-1"
        return [
            {
                "node_id": "title-1",
                "type": "title",
                "display_text": "Policy",
                "locator": {"page": 1, "bbox": [0, 0, 100, 20]},
            },
            {
                "node_id": "text-1",
                "type": "text",
                "display_text": "Warranty is three years.",
                "locator": {"page": 1, "bbox": [0, 30, 100, 60]},
            },
        ]


def test_mineru_provider_nodes_become_real_canonical_structure(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"synthetic-scan")
    parser = MinerUProductionParser(
        _Runner(),  # type: ignore[arg-type]
        _Store(),  # type: ignore[arg-type]
        source_format="pdf",
        is_ocr=True,
    )

    document = parser.parse(source, "version-1")

    assert document.real_acceptance is True
    assert [node.node_type for node in document.nodes] == [
        NodeType.HEADING,
        NodeType.PARAGRAPH,
    ]
    assert document.nodes[0].locator.bbox == (0.0, 0.0, 100.0, 20.0)
