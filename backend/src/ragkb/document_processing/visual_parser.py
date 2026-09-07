"""Enrich native/MinerU documents with verified, version-bound visual evidence."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from ragkb.adapters.visual_http import VisualAnalyzer
from ragkb.application.cancellation import check_cancelled
from ragkb.config import EnvSettings
from ragkb.contracts.ports import ParserPort, ParsingDeferred
from ragkb.document_processing.parser_common import canonical_document
from ragkb.document_processing.visual_coverage import inventory, render_fallback
from ragkb.document_processing.visual_processing import VisualProcessor
from ragkb.document_processing.visual_sources import SourceImage, native_images
from ragkb.domain.documents import CanonicalDocument, CanonicalNode, NodeType
from ragkb.domain.visuals import VisualExtraction
from ragkb.infrastructure.visual_assets import VisualAssetStore


class VisualDocumentParser:
    revision = "visual-document-v3:coverage-reviewed-revisions"

    def __init__(
        self,
        base: ParserPort,
        kind: str,
        analyzer: VisualAnalyzer,
        store: VisualAssetStore,
        settings: EnvSettings,
        *,
        scope: str = "local",
    ) -> None:
        self.scope = scope
        self.base, self.kind, self.analyzer, self.store, self.settings = (
            base,
            kind,
            analyzer,
            store,
            settings,
        )

    def artifact_keys(self, version_id: str) -> tuple[str, ...]:
        assets = self.store.list_assets(version_id)
        return (
            (self.store.manifest_key(version_id), *(a["storage_key"] for a in assets))
            if assets
            else ()
        )

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument:
        coverage = inventory(source, self.kind)
        source_images = native_images(
            source,
            "pdf" if self.kind == "pdf_scanned" else self.kind,
            self.settings.ocr_max_image_bytes,
        )
        if coverage["needs_render"] and self.settings.ocr_render_fallback_enabled:
            try:
                rendered, coverage = render_fallback(
                    source,
                    self.kind,
                    coverage,
                    self.settings,
                    self.store.storage.path_for("temp", "visual-render"),
                )
                if coverage.get("rendered_whole_document"):
                    source_images = rendered
                else:
                    rendered_pages = set(coverage.get("rendered_pages", []))
                    source_images = [
                        p for p in source_images if p.locator.page not in rendered_pages
                    ] + rendered
            except (ValueError, OSError) as error:
                coverage["render_error"] = (
                    str(error) if str(error).isupper() else "VISUAL_RENDER_FAILED"
                )
        self.store.ledger.put(
            "version", document_version_id, {"coverage": coverage, "stage": "inspecting"}
        )
        base_doc: CanonicalDocument | None = None
        if self.kind != "image" and not coverage.get("rendered_whole_document"):
            try:
                base_doc = self.base.parse(source, document_version_id)
            except ParsingDeferred as error:
                if error.code not in {"PARSE_EMPTY", "OCR_REQUIRED"} or not source_images:
                    raise
        if base_doc is not None:
            pages = set(coverage.get("rendered_pages", []))
            if pages:
                base_doc = replace(
                    base_doc, nodes=tuple(n for n in base_doc.nodes if n.locator.page not in pages)
                )
            # Legacy Office / scanned PDF uses retained MinerU assets with their actual bbox.
            supplied = getattr(getattr(self.base, "fallback", self.base), "visual_sources", None)
            if callable(supplied):
                source_images.extend(
                    p
                    for p in supplied(base_doc, self.settings.ocr_max_image_bytes)
                    if p.locator.page not in pages
                )
        if not source_images:
            if base_doc is None:
                raise ParsingDeferred("PARSE_EMPTY", "No usable text or pictures")
            incomplete = any(o["state"] == "unprocessed" for o in coverage["objects"])
            self.store.ledger.put(
                "version",
                document_version_id,
                {"coverage": coverage, "stage": "completed", "total_images": 0},
            )
            return (
                replace(
                    base_doc,
                    quality_issues=(*base_doc.quality_issues, "VISUAL_COVERAGE_INCOMPLETE"),
                )
                if incomplete
                else base_doc
            )
        if len(source_images) > self.settings.ocr_max_images_per_document:
            raise ParsingDeferred(
                "OCR_DOCUMENT_IMAGE_LIMIT", "Document exceeds configured image limit"
            )
        nodes = list(base_doc.nodes) if base_doc else []
        headings: list[tuple[int, str]] = []
        for index, node in enumerate(nodes):
            if node.node_type is NodeType.HEADING:
                level = int(node.metadata.get("heading_level", 1))
                headings = [(depth, title) for depth, title in headings if depth < level]
                headings.append((level, node.display_text.strip()))
            if "section_path" not in node.metadata:
                nodes[index] = replace(
                    node,
                    metadata={
                        **node.metadata,
                        "section_path": " / ".join(title for _, title in headings) or "root",
                    },
                )
        source_images = _bind_visual_sections(nodes, source_images)
        plan = self.store.ledger.get("version_plan", document_version_id)
        exclusions = plan.get("exclude_sections", [])

        def included(node: CanonicalNode) -> bool:
            path = str(node.metadata.get("section_path", "root"))
            return not any(
                path == section or path.startswith(section + " / ") for section in exclusions
            )

        nodes = [node for node in nodes if included(node)]
        visual_nodes: list[CanonicalNode] = []
        issues = list(base_doc.quality_issues) if base_doc else []
        unresolved = [
            o
            for o in coverage["objects"]
            if o["state"] == "unprocessed" and o["id"] not in plan.get("exclude_objects", [])
        ]
        if unresolved or coverage.get("missing_pages"):
            issues.append("VISUAL_COVERAGE_INCOMPLETE")
        if exclusions or plan.get("exclude_assets") or plan.get("exclude_objects"):
            issues.append("PARTIAL_PUBLICATION_CONTENT_EXCLUDED")
        processed = VisualProcessor(
            self.store, self.analyzer, self.settings, scope=self.scope
        ).process(document_version_id, source_images, coverage, source.stem)
        assets = [asset for _, asset in processed]
        for original, asset in processed:
            check_cancelled()
            if asset.get("status") == "excluded":
                continue
            parsed = (
                VisualExtraction.model_validate(asset["extraction"])
                if asset.get("extraction")
                else None
            )
            if asset["status"] == "verified" and parsed:
                # Full table transcription stays in the audit/UI. Search only complete,
                # structured rows, never a second flattened copy of the same cells.
                text = (
                    parsed.title
                    if parsed.kind == "table"
                    else parsed.retrieval_text(
                        include_tables=False, include_transcription=not parsed.tables
                    )
                )
                if original.context.strip():
                    text = (
                        "图片所在位置的原文上下文：\n"
                        + original.context
                        + "\n\n图片内容：\n"
                        + text
                    )
            else:
                issues.append("VISUAL_REVIEW_REQUIRED:" + asset["id"])
                text = "图片尚未通过原图核对，不能用于问答。"
            if not text.strip():
                text = "图片未包含可用于问答的信息。"
            metadata = {
                "visual_asset_ids": [asset["id"]],
                "visual_status": asset["status"],
                "document_title": source.stem,
                "section_path": original.section_path,
                "visual_caption": original.caption,
            }
            if not (parsed and parsed.kind == "table" and asset["status"] == "verified"):
                visual_nodes.append(
                    CanonicalNode(
                        node_id=hashlib.sha256(
                            (document_version_id + asset["id"]).encode()
                        ).hexdigest()[:32],
                        parent_node_id=None,
                        node_type=NodeType.IMAGE,
                        original_text=text,
                        display_text=text,
                        locator=original.locator,
                        metadata=metadata,
                    )
                )
            if parsed and asset["status"] == "verified":
                for index, table in enumerate(parsed.tables, 1):
                    content = table.as_html()
                    visual_nodes.append(
                        CanonicalNode(
                            node_id=hashlib.sha256(
                                (document_version_id + asset["id"] + f":table:{index}").encode()
                            ).hexdigest()[:32],
                            parent_node_id=None,
                            node_type=NodeType.TABLE,
                            original_text=content,
                            display_text=content,
                            locator=original.locator,
                            metadata={
                                **metadata,
                                "visual_table": table.model_dump(),
                                "visual_table_index": index,
                                "table_context": "\n".join(
                                    filter(
                                        None,
                                        [
                                            original.section_path
                                            if original.section_path != "root"
                                            else "",
                                            original.caption,
                                            parsed.title,
                                        ],
                                    )
                                ),
                            },
                        )
                    )
        nodes = _insert_visual_nodes(nodes, visual_nodes)
        if not nodes:
            raise ParsingDeferred("PARSE_EMPTY", "No independently publishable content remains")
        if base_doc is None:
            base_doc = canonical_document(
                source, document_version_id, self.kind, self.revision, nodes
            )
        return replace(
            base_doc,
            nodes=tuple(nodes),
            media_refs=tuple(assets),
            quality_issues=tuple(dict.fromkeys(issues)),
            parser_revision=base_doc.parser_revision + ":" + self.revision,
        )


def _insert_visual_nodes(
    base: list[CanonicalNode], pictures: list[CanonicalNode]
) -> list[CanonicalNode]:
    """Merge by real DOCX paragraph; keep exact provider placeholders or page anchors."""
    if not base or any(node.locator.paragraph is not None for node in base):
        return sorted(
            [*base, *pictures],
            key=lambda node: (
                node.locator.paragraph if node.locator.paragraph is not None else float("inf"),
                1 if node.metadata.get("visual_asset_ids") else 0,
            ),
        )
    # Replace a provider image/table placeholder with ALL recognized components.
    # Keeping its old flattened OCR would bypass both table slicing and rechecks.
    anchored: dict[int, list[CanonicalNode]] = {}
    unanchored: list[CanonicalNode] = []
    for picture in pictures:
        locator = picture.locator
        exact = next(
            (
                index
                for index, node in enumerate(base)
                if node.node_type in {NodeType.IMAGE, NodeType.TABLE}
                and not node.metadata.get("visual_asset_ids")
                and locator.bbox is not None
                and node.locator.bbox == locator.bbox
                and node.locator.page == locator.page
            ),
            None,
        )
        if exact is not None:
            anchored.setdefault(exact, []).append(picture)
        else:
            unanchored.append(picture)
    merged = [child for index, node in enumerate(base) for child in anchored.get(index, [node])]
    for picture in unanchored:
        locator = picture.locator
        if locator.slide is not None or locator.sheet is not None:
            matching = [
                index
                for index, node in enumerate(merged)
                if (locator.slide is not None and node.locator.slide == locator.slide)
                or (locator.sheet is not None and node.locator.sheet == locator.sheet)
            ]
            if matching:
                insert_at = next(
                    (
                        index
                        for index in matching
                        if locator.row is not None
                        and merged[index].locator.row is not None
                        and (merged[index].locator.row or 0) > locator.row
                    ),
                    matching[-1] + 1,
                )
                merged.insert(insert_at, picture)
                continue
        # Without a precise spatial anchor, retain page association and explicitly
        # use root scope instead of guessing the preceding product/section title.
        later_page = next(
            (
                index
                for index, node in enumerate(merged)
                if locator.page is not None
                and node.locator.page is not None
                and node.locator.page > locator.page
            ),
            len(merged),
        )
        merged.insert(later_page, picture)
    return merged


def _bind_visual_sections(
    base: list[CanonicalNode], pictures: list[SourceImage]
) -> list[SourceImage]:
    """Only a matching provider position can establish a page image's section."""
    paths: dict[tuple[int, tuple[float, ...]], str] = {}
    headings: list[tuple[int, str]] = []
    for node in base:
        if node.node_type is NodeType.HEADING:
            level = int(node.metadata.get("heading_level", 1))
            headings = [(depth, title) for depth, title in headings if depth < level]
            headings.append((level, node.display_text.strip()))
        if (
            node.node_type in {NodeType.IMAGE, NodeType.TABLE}
            and node.locator.page is not None
            and node.locator.bbox is not None
        ):
            paths[(node.locator.page, node.locator.bbox)] = str(
                node.metadata.get("section_path")
                or " / ".join(title for _, title in headings)
                or "root"
            )
    return [
        replace(picture, section_path=paths[(picture.locator.page, picture.locator.bbox)])
        if picture.section_path == "root"
        and picture.locator.page is not None
        and picture.locator.bbox is not None
        and (picture.locator.page, picture.locator.bbox) in paths
        else picture
        for picture in pictures
    ]
