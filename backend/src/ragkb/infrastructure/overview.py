"""Read authorized current chapters in order, reduce to original quotes, report coverage."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any

from ragkb.adapters.chapter_reader import ChapterReader
from ragkb.application.authorization import ResourceAuthorizationService
from ragkb.application.cancellation import check_cancelled
from ragkb.application.provider_budget import ConservativeTokenCounter
from ragkb.application.reading_scope import options, progress
from ragkb.config import EnvSettings
from ragkb.contracts.uploads import UploadRepositoryPort
from ragkb.domain.errors import InvalidProviderResponse, TransientProviderError
from ragkb.domain.pagination import PageKey
from ragkb.domain.rag import Evidence
from ragkb.domain.retrieval import SearchContext
from ragkb.infrastructure.model_account import provider_operation
from ragkb.infrastructure.visual_assets import VisualAssetStore
from ragkb.infrastructure.visual_evidence import VisualEvidenceEnricher


class OverviewReader:
    def __init__(
        self,
        repository: UploadRepositoryPort,
        authorization: ResourceAuthorizationService,
        store: VisualAssetStore,
        settings: EnvSettings,
        reader: ChapterReader,
        visual: VisualEvidenceEnricher | None = None,
    ) -> None:
        self.repository, self.authorization, self.store = repository, authorization, store
        self.settings, self.reader, self.visual = settings, reader, visual

    def related_sources(
        self, evidence: tuple[Evidence, ...], context: SearchContext
    ) -> tuple[Evidence, ...]:
        """Follow explicit figure references, with the same version and authorization checks."""
        result: list[Evidence] = []
        for version in dict.fromkeys(e.document_version_id for e in evidence):
            subset = [e for e in evidence if e.document_version_id == version]
            links = explicit_image_links(self.store.list_assets(version), subset)
            present = {
                identity for e in subset for identity in e.locator.get("visual_asset_ids", [])
            }
            wanted = {link["target_asset_id"] for link in links} - present
            if not wanted:
                continue
            cursor: PageKey | None = None
            scanned = 0
            while scanned < self.settings.overview_max_chunks:
                check_cancelled()
                page = self.repository.list_chunks_page(
                    version, limit=100, after=cursor, context=context
                )
                scanned += 100
                ids = [
                    r["chunk_id"]
                    for r in page.items
                    if not r.get("is_parent")
                    and wanted.intersection(r["locator"].get("visual_asset_ids", []))
                ]
                for chunk in self.authorization.authorize_chunks(ids, context).values():
                    result.append(
                        Evidence(
                            evidence_id=f"E{len(result) + 1}",
                            chunk_id=chunk.chunk_id,
                            document_id=chunk.document_id,
                            document_version_id=version,
                            text=chunk.retrieval_text or chunk.display_text,
                            display_text=chunk.display_text,
                            locator={
                                **chunk.locator,
                                "association_basis": "explicit_figure_reference",
                            },
                            valid_from_epoch=chunk.valid_from_epoch,
                            valid_to_epoch=chunk.valid_to_epoch,
                            authority_rank=0,
                            permission_revision=chunk.permission_revision,
                            authorized=True,
                            current_version=True,
                        )
                    )
                    wanted.difference_update(chunk.locator.get("visual_asset_ids", []))
                if not wanted or page.next_key is None:
                    break
                cursor = page.next_key
        return tuple(result)

    def read(
        self, question: str, context: SearchContext
    ) -> tuple[tuple[Evidence, ...], dict[str, Any]]:
        selected = set(options.get().document_ids)
        evidence: list[Evidence] = []
        sections: dict[tuple[str, str], list[Evidence]] = {}
        documents: dict[str, dict[str, Any]] = {}
        after: PageKey | None = None
        exhausted = True
        while True:
            check_cancelled()
            page = self.repository.list_documents_page(
                context.space_ids[0], current_only=True, limit=50, after=after
            )
            for document in page.items:
                if selected and document["document_id"] not in selected:
                    continue
                version = document["version_id"]
                cursor: PageKey | None = None
                while True:
                    check_cancelled()
                    chunks = self.repository.list_chunks_page(
                        version, limit=100, context=context, after=cursor
                    )
                    authorized = self.authorization.authorize_chunks(
                        [r["chunk_id"] for r in chunks.items if not r.get("is_parent")], context
                    )
                    for row in chunks.items:
                        source = authorized.get(row["chunk_id"])
                        if source is None or source.locator.get("is_parent"):
                            continue
                        if len(evidence) >= self.settings.overview_max_chunks:
                            exhausted = False
                            break
                        section = str(
                            source.locator.get("section_path")
                            or (
                                f"第 {source.locator['page']} 页"
                                if source.locator.get("page")
                                else "未分章内容"
                            )
                        )
                        item = Evidence(
                            evidence_id=f"E{len(evidence) + 1}",
                            chunk_id=source.chunk_id,
                            document_id=source.document_id,
                            document_version_id=version,
                            text=source.retrieval_text or source.display_text,
                            display_text=source.display_text,
                            locator={
                                **source.locator,
                                "section_path": section,
                                "filename": document["filename"],
                            },
                            valid_from_epoch=source.valid_from_epoch,
                            valid_to_epoch=source.valid_to_epoch,
                            authority_rank=0,
                            permission_revision=source.permission_revision,
                            authorized=True,
                            current_version=True,
                        )
                        evidence.append(item)
                        sections.setdefault((version, section), []).append(item)
                        documents[source.document_id] = document
                    if not exhausted or chunks.next_key is None:
                        break
                    cursor = chunks.next_key
                if not exhausted:
                    break
            if not exhausted or page.next_key is None:
                break
            after = page.next_key
        report: dict[str, Any] = {
            "mode": "overview",
            "scope_complete": exhausted,
            "read_chunks": len(evidence),
            "read_sections": 0,
            "total_sections": len(sections),
            "source_documents": [
                {
                    "document_id": d["document_id"],
                    "version_id": d["version_id"],
                    "filename": d["filename"],
                }
                for d in documents.values()
            ],
            "sections": [],
            "gaps": [],
            "cross_image_links": [],
        }
        if not exhausted:
            report["gaps"].append("达到本次读取片段上限，后续章节尚未读取")
        if selected - documents.keys():
            report["gaps"].append("部分指定文档没有当前可读取内容，请检查发布状态和访问范围")
        for doc in documents.values():
            state = self.store.ledger.get("version", doc["version_id"])
            removed = state.get("plan", {}).get("exclude_sections", [])
            if removed:
                report["gaps"].append(doc["filename"] + " 已排除章节：" + "、".join(removed))
            assets = self.store.list_assets(doc["version_id"])
            if assets and state.get("coverage", {}).get("inspection") != "inspected":
                report["gaps"].append(doc["filename"] + "：历史版本尚未检测图片提取覆盖范围")
            report["cross_image_links"].extend(
                explicit_image_links(
                    assets, [e for e in evidence if e.document_version_id == doc["version_id"]]
                )
            )
        session = None
        callback = progress.get()
        if callback:
            callback(report)
        if self.visual:
            session = VisualEvidenceEnricher(
                self.visual.store, self.visual.analyzer, self.settings.overview_max_images
            ).session(question)
        counter = ConservativeTokenCounter()
        total_tokens = sum(counter.count(e.text) for e in evidence)
        reduced: list[list[Evidence]] = []
        for (version, section), contents in sections.items():
            check_cancelled()
            checked = session(tuple(contents)) if session else tuple(contents)
            section_report: dict[str, Any] = {
                "version_id": version,
                "section": section,
                "chunks": len(contents),
                "usable_chunks": len(checked),
                "state": "read",
            }
            if len(checked) != len(contents):
                section_report["state"] = "incomplete"
                report["gaps"].append(section + "：部分图片未通过本轮核对或超过看图预算")
            output = list(checked)
            if total_tokens > self.settings.overview_evidence_tokens:
                output = []
                # Every chunk is visited; never cut a table row or arbitrary suffix.
                batches: list[list[Evidence]] = [[]]
                used = 0
                for item in checked:
                    size = counter.count(item.text)
                    if size > 8000:
                        report["gaps"].append(section + "：单块超过章节读取预算")
                        section_report["state"] = "incomplete"
                        continue
                    if used + size > 8000:
                        batches.append([])
                        used = 0
                    batches[-1].append(item)
                    used += size
                for batch in batches:
                    if not batch:
                        continue
                    key = hashlib.sha256(
                        json.dumps(
                            [self.reader.revision, question, [(e.chunk_id, e.text) for e in batch]],
                            ensure_ascii=False,
                        ).encode()
                    ).hexdigest()
                    try:
                        brief = self.store.ledger.cache_get(context.tenant_id + ":chapter", key)
                        if not brief:
                            with provider_operation(version, "", "chapter_reading"):
                                brief = self.reader.read(question, tuple(batch))
                            brief["_source_version_id"] = version
                            self.store.ledger.cache_put(
                                context.tenant_id + ":chapter",
                                key,
                                brief,
                                self.settings.ocr_generation_cache_ttl_seconds,
                            )
                        by_id = {e.chunk_id: e for e in batch}
                        for quote in brief["quotes"]:
                            quote_source = by_id.get(quote["chunk_id"])
                            if quote_source is None or quote["quote"] not in quote_source.text:
                                raise InvalidProviderResponse("CHAPTER_CACHE_SOURCE_CHANGED")
                            # Keep whole sources, including conditions, for final verification.
                            if quote_source.chunk_id not in {e.chunk_id for e in output}:
                                output.append(quote_source)
                        if brief.get("gaps"):
                            report["gaps"].extend(section + "：" + g for g in brief["gaps"])
                    except (InvalidProviderResponse, TransientProviderError):
                        section_report["state"] = "incomplete"
                        report["gaps"].append(section + "：章节提要生成失败，保留原文待重试")
            reduced.append(output)
            report["sections"].append(section_report)
            report["read_sections"] += 1
            report["checked_images"] = session.attempted if session else 0
            callback = progress.get()
            if callback:
                callback(report)
        # Round-robin across chapters, so an early long chapter cannot consume every slot.
        selected_evidence: list[Evidence] = []
        used = 0
        for index in range(max((len(batch) for batch in reduced), default=0)):
            for batch in reduced:
                if index >= len(batch):
                    continue
                item = batch[index]
                size = counter.count(item.text)
                if used + size > self.settings.overview_evidence_tokens:
                    report["gaps"].append(item.locator["section_path"] + "：最终回答证据预算不足")
                    continue
                selected_evidence.append(item)
                used += size
        report["gaps"] = list(dict.fromkeys(report["gaps"]))
        report["complete"] = exhausted and not report["gaps"]
        report["evidence_chunks"] = len(selected_evidence)
        report["checked_images"] = session.attempted if session else 0
        report["image_checks"] = [
            {
                "version_id": version,
                "asset_id": asset,
                "status": outcome.status,
                "issues": list(outcome.issues),
            }
            for (version, asset), outcome in (session.checked.items() if session else [])
        ]
        return tuple(
            replace(
                e,
                evidence_id=f"E{i}",
                locator={**e.locator, "reading_coverage_complete": report["complete"]},
            )
            for i, e in enumerate(selected_evidence, 1)
        ), report


def explicit_image_links(
    assets: list[dict[str, Any]], evidence: list[Evidence]
) -> list[dict[str, Any]]:
    """Only unique, explicit figure references in one version establish a relationship."""
    pattern = r"(?:图|Figure)\s*\d+(?:[.\-]\d+)*"
    labels: dict[str, list[str]] = {}
    for asset in assets:
        if asset.get("status") != "verified":
            continue
        for label in re.findall(pattern, asset.get("caption", ""), re.I):
            labels.setdefault(re.sub(r"\s", "", label).casefold(), []).append(asset["id"])
    links = []
    for item in evidence:
        for label in re.findall(pattern, item.text, re.I):
            targets = labels.get(re.sub(r"\s", "", label).casefold(), [])
            if len(set(targets)) == 1:
                links.append(
                    {
                        "source_chunk_id": item.chunk_id,
                        "target_asset_id": targets[0],
                        "reference": label,
                        "basis": "explicit_figure_reference",
                    }
                )
    return links
