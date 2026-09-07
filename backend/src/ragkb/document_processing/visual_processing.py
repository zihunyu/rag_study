"""Bounded parallel image processing with durable stages, immutable edits and safe reuse."""

from __future__ import annotations

import hashlib
import io
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from typing import Any

from PIL import Image

from ragkb.adapters.visual_http import VisualAnalyzer
from ragkb.application.cancellation import check_cancelled
from ragkb.config import EnvSettings
from ragkb.document_processing.local_visual_check import (
    LOCAL_CHECK_REVISION,
    check_extraction,
    read_regions,
)
from ragkb.document_processing.visual_sources import SourceImage
from ragkb.domain.errors import IngestionCancelled
from ragkb.domain.visuals import VisualExtraction, reviewed_extraction
from ragkb.infrastructure.model_account import provider_operation
from ragkb.infrastructure.visual_assets import VisualAssetStore


class VisualProcessor:
    def __init__(
        self,
        store: VisualAssetStore,
        analyzer: VisualAnalyzer,
        settings: EnvSettings,
        *,
        scope: str = "local",
    ) -> None:
        self.store, self.analyzer, self.settings, self.scope = store, analyzer, settings, scope

    def process(
        self, version: str, pictures: list[SourceImage], coverage: dict[str, Any], filename: str
    ) -> list[tuple[SourceImage, dict[str, Any]]]:
        run = uuid.uuid4().hex
        plan = self.store.ledger.get("version_plan", version)
        old = {a["id"]: a for a in self.store.list_assets(version)}
        if plan.get("base_version_id"):
            old = {a["id"]: a for a in self.store.list_assets(plan["base_version_id"])} | old
        self.store.ledger.put(
            "version",
            version,
            {
                "run": run,
                "stage": "processing",
                "coverage": coverage,
                "started_at": time.time(),
                "plan": plan,
                "total_images": len(pictures),
            },
        )
        prepared: list[tuple[SourceImage, dict[str, Any]]] = []
        seen = set()
        for picture in pictures:
            asset = self.store.save_image(version, picture.data, picture.locator.to_dict())
            if asset["id"] in seen:
                continue
            seen.add(asset["id"])
            asset.update(
                section_path=picture.section_path,
                caption=picture.caption,
                context=picture.context,
                ordinal=len(prepared),
                status="queued",
                stage="queued",
                updated_at=time.time(),
            )
            self.store.ledger.upsert_asset(version, asset, run=run)
            prepared.append((picture, asset))

        def analyze(pair: tuple[SourceImage, dict[str, Any]]) -> tuple[SourceImage, dict[str, Any]]:
            picture, asset = pair
            identity = asset["id"]

            def stage(name: str) -> None:
                check_cancelled()
                asset.update(stage=name, updated_at=time.time())
                self.store.ledger.upsert_asset(version, asset, run=run)

            try:
                check_cancelled()
                context_hash = hashlib.sha256(
                    (
                        picture.section_path + "\n" + picture.caption + "\n" + picture.context
                    ).encode()
                ).hexdigest()
                key = hashlib.sha256(
                    (asset["sha256"] + context_hash + self.analyzer.revision).encode()
                ).hexdigest()
                prior = old.get(identity)
                edited = plan.get("edits", {}).get(identity)
                retry = identity in plan.get("retry_assets", [])
                excluded = identity in plan.get("exclude_assets", []) or any(
                    picture.section_path == path or picture.section_path.startswith(path + " / ")
                    for path in plan.get("exclude_sections", [])
                )
                if excluded:
                    asset.update(
                        status="excluded",
                        origin="human_exclusion",
                        issues=[],
                        extraction=None,
                        revision=self.analyzer.revision,
                    )
                    stage("excluded")
                    return picture, asset
                if edited:
                    extraction = VisualExtraction.model_validate(edited)
                    if prior and prior.get("extraction"):
                        extraction = reviewed_extraction(
                            VisualExtraction.model_validate(prior["extraction"]), extraction
                        )
                    if extraction.issues():
                        raise ValueError("VISUAL_UNRESOLVED_UNCERTAINTIES")
                    asset.update(
                        extraction=extraction.model_dump(),
                        status="verified",
                        issues=[],
                        origin="human_review",
                        history=(prior or {}).get("history", []) + plan.get("history", []),
                        revision=self.analyzer.revision,
                        analyzed_at=time.time(),
                    )
                else:
                    cached = None
                    if (
                        not retry
                        and prior
                        and prior.get("status") == "verified"
                        and prior.get("context_sha256") == context_hash
                        and prior.get("revision") == self.analyzer.revision
                        and (
                            bool(plan.get("base_version_id"))
                            or 0
                            <= time.time() - prior.get("analyzed_at", 0)
                            < self.settings.ocr_generation_cache_ttl_seconds
                        )
                    ):
                        cached = prior
                    if not cached and not retry and self.settings.ocr_cross_version_cache_enabled:
                        cached = self.store.ledger.cache_get(self.scope, key)
                    if cached:
                        binding = dict(asset)
                        asset.update(cached)
                        asset.update(
                            {
                                k: binding[k]
                                for k in (
                                    "id",
                                    "storage_key",
                                    "sha256",
                                    "locator",
                                    "ordinal",
                                    "section_path",
                                    "caption",
                                    "context",
                                )
                            }
                        )
                        asset.update(
                            cache_hit=True,
                            origin="human_review"
                            if cached.get("origin") == "human_review"
                            else "cache",
                            status="verified",
                        )
                        self.store.ledger.usage(
                            version,
                            identity,
                            role="ocr",
                            model=self.settings.ocr_model,
                            started=time.time(),
                            outcome="cache_hit",
                            cached=True,
                        )
                    else:
                        with provider_operation(version, identity, "ocr"):
                            asset.update(
                                self.analyzer.analyze(picture.data, picture.context, progress=stage)
                            )
                        asset.update(origin="model", cache_hit=False)
                asset["context_sha256"] = context_hash
                asset["_source_version_id"] = version
                with Image.open(io.BytesIO(picture.data)) as image:
                    asset.update(width=image.width, height=image.height)
                if (
                    self.settings.ocr_local_check_enabled
                    and asset.get("local_check", {}).get("revision") != LOCAL_CHECK_REVISION
                ):
                    stage("checking_text")
                    reading = read_regions(picture.data, self.settings)
                    asset.update(
                        width=reading["width"],
                        height=reading["height"],
                        regions=reading["regions"],
                        coordinate_space=reading["coordinate_space"],
                    )
                    if asset.get("extraction"):
                        check = check_extraction(
                            VisualExtraction.model_validate(asset["extraction"]), reading
                        )
                        asset["local_check"] = check
                        if (
                            check["status"] != "consistent"
                            and asset.get("origin") != "human_review"
                        ):
                            asset.update(
                                status="needs_review",
                                issues=list(asset.get("issues", []))
                                + check["issues"]
                                + (
                                    [
                                        "独立 OCR 无法确认以下数字或单位："
                                        + "、".join(check["unmatched_critical_tokens"])
                                    ]
                                    if check["unmatched_critical_tokens"]
                                    else []
                                ),
                            )
                if edited:
                    asset["source_asset_id"] = identity
                    asset["source_version_id"] = plan.get("base_version_id")
                if (
                    asset.get("status") == "verified"
                    and not edited
                    and asset.get("origin") != "human_review"
                    and self.settings.ocr_cross_version_cache_enabled
                ):
                    self.store.ledger.cache_put(
                        self.scope,
                        key,
                        {
                            k: v
                            for k, v in asset.items()
                            if k
                            in {
                                "status",
                                "extraction",
                                "issues",
                                "audit",
                                "revision",
                                "analyzed_at",
                                "verification_mode",
                                "regions",
                                "local_check",
                                "width",
                                "height",
                                "coordinate_space",
                                "_source_version_id",
                            }
                        },
                        self.settings.ocr_generation_cache_ttl_seconds,
                    )
                stage("completed" if asset.get("status") == "verified" else "needs_review")
            except IngestionCancelled:
                raise
            except Exception as error:
                asset.update(
                    status="failed",
                    stage="failed",
                    issues=[type(error).__name__],
                    error_code=str(error)
                    if str(error).isupper() and len(str(error)) < 100
                    else "VISUAL_PROCESSING_FAILED",
                )
                self.store.ledger.upsert_asset(version, asset, run=run)
                raise
            return picture, asset

        completed: dict[str, tuple[SourceImage, dict[str, Any]]] = {}
        failures = []
        with ThreadPoolExecutor(
            max_workers=self.settings.ocr_max_concurrency, thread_name_prefix="visual"
        ) as executor:
            futures = [executor.submit(copy_context().run, analyze, pair) for pair in prepared]
            for future in as_completed(futures):
                try:
                    pair = future.result()
                    completed[pair[1]["id"]] = pair
                except Exception as error:
                    failures.append(error)
        state = self.store.ledger.get("version", version)
        check_cancelled()
        if state.get("run") != run:
            raise ValueError("VISUAL_EXECUTION_SUPERSEDED")
        state.update(stage="failed" if failures else "completed", finished_at=time.time())
        self.store.ledger.put("version", version, state, expected=state["row_version"])
        if failures:
            raise failures[0]
        result = [completed[pair[1]["id"]] for pair in prepared]
        # Each asset is already committed with the run fence. Do not rewrite a
        # manifest after completion: a newer worker may have acquired this version.
        return result
