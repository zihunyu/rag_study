"""Production MinerU parser converting validated provider nodes to the canonical contract."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ragkb.application.provider_runners import MinerUExecutionRunner
from ragkb.document_processing.parser_common import canonical_document, checksum
from ragkb.domain.documents import CanonicalDocument, CanonicalNode, NodeType, SourceLocator
from ragkb.infrastructure.provider_results import LocalProviderResultStore

_NODE_TYPES = {
    "title": NodeType.HEADING,
    "table": NodeType.TABLE,
    "code": NodeType.CODE,
    "list": NodeType.LIST,
    "image": NodeType.IMAGE,
}


class MinerUProductionParser:
    revision = "mineru-production-canonical:v1"

    def __init__(
        self,
        runner: MinerUExecutionRunner,
        result_store: LocalProviderResultStore,
        *,
        source_format: str,
        is_ocr: bool,
    ) -> None:
        self.runner = runner
        self.result_store = result_store
        self.source_format = source_format
        self.is_ocr = is_ocr

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument:
        source_hash = checksum(source)
        anonymous_id = hashlib.sha256(
            f"{document_version_id}:{source_hash}".encode(), usedforsecurity=False
        ).hexdigest()[:24]
        evidence = self.runner.run_file(
            source,
            anonymous_id,
            source_hash,
            is_ocr=self.is_ocr,
        )
        artifact_id = evidence.get("artifact_id")
        if not isinstance(artifact_id, str):
            raise ValueError("MINERU_ARTIFACT_ID_MISSING")
        raw_nodes = self.result_store.read_mineru_nodes(artifact_id)
        nodes: list[CanonicalNode] = []
        for raw in raw_nodes:
            text = str(raw.get("display_text", "")).strip()
            locator = raw.get("locator")
            if not text or not isinstance(locator, dict):
                continue
            page = locator.get("page")
            bbox = locator.get("bbox")
            if not isinstance(page, int) or page < 1:
                raise ValueError("MINERU_CANONICAL_LOCATOR_INVALID")
            typed_bbox = (
                (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
                if isinstance(bbox, list) and len(bbox) == 4
                else None
            )
            item_type = str(raw.get("type", "text")).casefold()
            metadata: dict[str, Any] = {
                "mineru_type": item_type,
                "artifact_id": artifact_id,
            }
            if item_type == "title":
                metadata["heading_level"] = 1
            nodes.append(
                CanonicalNode(
                    node_id=str(raw.get("node_id", "")),
                    parent_node_id=None,
                    node_type=_NODE_TYPES.get(item_type, NodeType.PARAGRAPH),
                    original_text=text,
                    display_text=text,
                    locator=SourceLocator(page=page, bbox=typed_bbox),
                    metadata=metadata,
                )
            )
        if not nodes:
            raise ValueError("MINERU_CANONICAL_DOCUMENT_EMPTY")
        return canonical_document(
            source,
            document_version_id,
            self.source_format,
            self.revision,
            nodes,
            real_acceptance=True,
        )
