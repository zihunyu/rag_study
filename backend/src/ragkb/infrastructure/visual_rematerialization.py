"""Deterministic new-version materialization of already reviewed visual source facts."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ragkb.application.cancellation import check_cancelled
from ragkb.domain.documents import CanonicalDocument, CanonicalNode, NodeType, SourceLocator
from ragkb.domain.graph_facts import graph_facts
from ragkb.domain.visuals import VisualExtraction
from ragkb.infrastructure.visual_assets import VisualAssetStore

if TYPE_CHECKING:
    from ragkb.runtime_components import RuntimeComponents

REVISION = "reviewed-graph-materialization-v2-semantic-branches"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()


def canonical_from_dict(raw: dict[str, Any]) -> CanonicalDocument:
    nodes = []
    for value in raw["nodes"]:
        location = dict(value["locator"])
        for key in ("bbox", "char_range"):
            if key in location:
                location[key] = tuple(location[key])
        nodes.append(
            CanonicalNode(
                node_id=value["node_id"],
                parent_node_id=value.get("parent_node_id"),
                node_type=NodeType(value["type"]),
                original_text=value["original_text"],
                display_text=value["display_text"],
                locator=SourceLocator(**location),
                metadata=value.get("metadata", {}),
            )
        )
    return CanonicalDocument(
        document_version_id=raw["document_version_id"],
        language=raw["language"],
        source_format=raw["source_format"],
        nodes=tuple(nodes),
        parser_revision=raw["parser_revision"],
        normalization_revision=raw["normalization_revision"],
        content_checksum=raw["content_checksum"],
        tables=tuple(raw.get("tables", [])),
        media_refs=tuple(raw.get("media_refs", [])),
        quality_issues=tuple(raw.get("quality_issues", [])),
        contract_version=raw.get("contract_version", "1.0"),
        real_acceptance=raw.get("real_acceptance", False),
    )


def materialized_visual_text(extracted: VisualExtraction) -> str:
    """Drop only provably redundant transcription/old derived lines from the search view.

    Human body/notes remain in the immutable extraction and in this view. In particular,
    this is not a model rewrite and does not infer which human sentences are expendable.
    """
    if not extracted.graphs:
        return extracted.retrieval_text(
            include_tables=False, include_transcription=not extracted.tables
        )
    if any(
        item.review_status in {"pending", "excluded"}
        for graph in extracted.graphs
        for item in [*graph.groups, *graph.nodes, *graph.edges]
    ):
        return extracted.retrieval_text(include_tables=False, include_transcription=False)
    legacy_lines = set()
    atomic_lines: set[str] = set()
    for graph in extracted.graphs:
        labels = {**{g.id: g.label for g in graph.groups}, **{n.id: n.label for n in graph.nodes}}
        atomic_lines.update(
            line.strip() for value in labels.values() for line in value.splitlines()
        )
        for node in graph.nodes:
            legacy_lines.add(f"组件：{node.label}")
        for group in graph.groups:
            children = [n.label for n in graph.nodes if n.group == group.id and n.label]
            legacy_lines.add(f"分组 {group.label} 包含：{'、'.join(children)}")
        for edge in graph.edges:
            relation = {"forward": "指向", "both": "双向连接", "none": "相连（无箭头）"}[
                edge.direction
            ]
            legacy_lines.add(
                f"{labels[edge.source]} {relation} {labels[edge.target]}；{edge.label}"
                + ("；虚线" if edge.style == "dashed" else "")
            )
            atomic_lines.update(
                line.strip()
                for value in (edge.label, edge.condition)
                for line in value.splitlines()
            )
    legacy_lines = {line.strip() for line in legacy_lines}

    def clean_prose(value: str) -> str:
        lines = []
        for line in value.splitlines():
            if line.strip() in legacy_lines:
                continue
            if re.search(r"(?:指向|双向连接|相连（无箭头）)\s*[；;]", line):
                raise ValueError("VISUAL_REMATERIALIZATION_UNBOUND_HUMAN_PROSE_REQUIRES_REVIEW")
            lines.append(line)
        return "\n".join(lines).strip()

    body, description = clean_prose(extracted.body_text), clean_prose(extracted.description)
    atomic_lines.update(
        line.strip()
        for value in (extracted.title, body, description)
        for line in value.splitlines()
    )
    transcription = "\n".join(
        line
        for line in clean_prose(extracted.transcription).splitlines()
        if line.strip() and line.strip() not in atomic_lines
    )
    parts = [extracted.title, description, body, transcription]
    for index, graph in enumerate(extracted.graphs):
        parts.extend(
            fact.text for fact in graph_facts(graph, scope=f"materialize:{index}", verified=True)
        )
    return "\n\n".join(dict.fromkeys(part.strip() for part in parts if part.strip()))


def validate_reviewed_assets(assets: list[dict[str, Any]], canonical: dict[str, Any]) -> None:
    if not assets or not any(
        a.get("extraction", {}).get("graphs") for a in assets if a.get("extraction")
    ):
        raise ValueError("VISUAL_REMATERIALIZATION_REVIEWED_GRAPH_REQUIRED")
    included = set()
    for asset in assets:
        if asset.get("status") == "excluded" and asset.get("origin") == "human_exclusion":
            continue
        if (
            asset.get("origin") != "human_review"
            or asset.get("status") != "verified"
            or not asset.get("extraction")
        ):
            raise ValueError("VISUAL_REMATERIALIZATION_HUMAN_REVIEW_REQUIRED")
        if not any(
            isinstance(event, dict) and event.get("actor") and event.get("reason")
            for event in asset.get("history", [])
        ):
            raise ValueError("VISUAL_REMATERIALIZATION_HUMAN_RECEIPT_MISSING")
        extracted = VisualExtraction.model_validate(asset["extraction"])
        if extracted.issues() or any(
            item.review_status == "pending"
            for graph in extracted.graphs
            for item in [*graph.groups, *graph.nodes, *graph.edges]
        ):
            raise ValueError("VISUAL_REMATERIALIZATION_UNRESOLVED_SOURCE")
        included.add(asset["id"])
    linked = {
        identity
        for node in canonical["nodes"]
        for identity in node.get("metadata", {}).get("visual_asset_ids", [])
    }
    if linked != included:
        raise ValueError("VISUAL_REMATERIALIZATION_CANONICAL_BINDING_MISMATCH")


def rematerialize_canonical(snapshot: dict[str, Any], new_version: str) -> CanonicalDocument:
    """Reuse unchanged native paragraphs and source positions; never rerun OCR or a parser."""
    old = canonical_from_dict(snapshot["canonical"])
    assets = {a["id"]: a for a in snapshot["assets"]}
    validate_reviewed_assets(list(assets.values()), snapshot["canonical"])
    aliases = {
        node.node_id: hashlib.sha256(f"{new_version}:{node.node_id}".encode()).hexdigest()[:32]
        for node in old.nodes
    }
    nodes = []
    for node in old.nodes:
        text = node.original_text
        display = node.display_text
        identities = node.metadata.get("visual_asset_ids", [])
        if identities and node.node_type is NodeType.IMAGE:
            text = (
                "\n\n".join(
                    materialized_visual_text(
                        VisualExtraction.model_validate(assets[identity]["extraction"])
                    )
                    for identity in identities
                ).strip()
                or "该图片没有已确认的可问答事实。"
            )
            display = text
        if node.parent_node_id and node.parent_node_id not in aliases:
            raise ValueError("VISUAL_REMATERIALIZATION_PARENT_MISMATCH")
        nodes.append(
            replace(
                node,
                node_id=aliases[node.node_id],
                parent_node_id=aliases.get(node.parent_node_id) if node.parent_node_id else None,
                original_text=text,
                display_text=display,
            )
        )
    return replace(
        old,
        document_version_id=new_version,
        nodes=tuple(nodes),
        parser_revision=f"{old.parser_revision}:{REVISION}",
    )


def build_snapshot(runtime: RuntimeComponents, version: dict[str, Any]) -> dict[str, Any]:
    document_id, version_id = str(version["document_id"]), str(version["id"])
    document = runtime.repository.get_document(document_id)
    versions = runtime.repository.get_versions(document_id)
    latest = max(versions, key=lambda v: int(v["version_no"]))
    runtime.lifecycle_store.reload()
    lifecycle = runtime.lifecycle_store.documents.get(document_id)
    if latest["id"] != version_id:
        raise ValueError("VISUAL_REMATERIALIZATION_SOURCE_NOT_LATEST")
    if (
        not lifecycle
        or lifecycle.lifecycle_state.value != "ACTIVE"
        or lifecycle.active_version_id != version_id
        or lifecycle.tombstoned
        or version.get("publication_state") != "SERVING"
        or version.get("processing_state") != "VALIDATED"
        or document.get("state") != "ACTIVE"
    ):
        raise ValueError("VISUAL_REMATERIALIZATION_SOURCE_NOT_ACTIVE")
    original = runtime.storage.path_for("original", str(version["original_key"]))
    original_sha = hashlib.sha256(original.read_bytes()).hexdigest()
    if original_sha != version.get("content_sha256"):
        raise ValueError("VISUAL_REMATERIALIZATION_ORIGINAL_CHANGED")
    prefix, separator, _ = str(version["original_key"]).rpartition("/original/")
    if not separator:
        raise ValueError("VISUAL_REMATERIALIZATION_CANONICAL_MISSING")
    folder = runtime.storage.path_for("artifacts", prefix + "/artifacts")
    candidates = []
    for path in folder.glob("canonical-document*.json"):
        matched = re.fullmatch(r"canonical-document(?:-f(\d+))?\.json", path.name)
        if matched:
            candidates.append((int(matched[1] or 0), path))
    if not candidates:
        raise ValueError("VISUAL_REMATERIALIZATION_CANONICAL_MISSING")
    canonical = json.loads(max(candidates)[1].read_text(encoding="utf-8"))
    if canonical.get("source_format") not in {
        "pdf",
        "pdf_scanned",
        "doc",
        "docx",
        "ppt",
        "pptx",
        "xlsx",
        "image",
    }:
        raise ValueError("VISUAL_REMATERIALIZATION_PARSER_ROUTE_UNSUPPORTED")
    if (
        canonical["document_version_id"] != version_id
        or canonical["content_checksum"] != original_sha
    ):
        raise ValueError("VISUAL_REMATERIALIZATION_CANONICAL_SOURCE_MISMATCH")
    store = VisualAssetStore(runtime.storage)
    assets = store.list_assets(version_id)
    validate_reviewed_assets(assets, canonical)
    if all(asset.get("materialization_revision") == REVISION for asset in assets):
        raise ValueError("VISUAL_REMATERIALIZATION_ALREADY_CURRENT")
    for asset in assets:
        store.read_image(version_id, asset)  # Fail before mutation on missing/corrupted originals.
    chunks = []
    offset = 0
    while True:
        page = runtime.repository.list_chunks(version_id, limit=100, offset=offset, preview=True)
        chunks.extend(page)
        if len(page) < 100:
            break
        offset += len(page)
    snapshot: dict[str, Any] = json.loads(
        json.dumps(
            {
                "generator_revision": REVISION,
                "document": document,
                "version": version,
                "versions": versions,
                "lifecycle": asdict(lifecycle),
                "original_sha256": original_sha,
                "canonical": canonical,
                "assets": assets,
                "chunks": chunks,
                "version_plan": store.ledger.get("version_plan", version_id),
                "visual_processing": store.ledger.get("version", version_id),
            },
            default=str,
            ensure_ascii=False,
        )
    )
    # Validate the exact deterministic projection before returning an actionable plan.
    rematerialize_canonical(snapshot, "dry-run")
    return snapshot


def apply_snapshot(
    store: VisualAssetStore, source: Path, version: str, plan: dict[str, Any]
) -> CanonicalDocument:
    snapshot = plan["rematerialization"]["snapshot"]
    expected = plan["rematerialization"]["fingerprint"]
    if digest(snapshot) != expected or snapshot["generator_revision"] != REVISION:
        raise ValueError("VISUAL_REMATERIALIZATION_PLAN_INTEGRITY_INVALID")
    if hashlib.sha256(source.read_bytes()).hexdigest() != snapshot["original_sha256"]:
        raise ValueError("VISUAL_REMATERIALIZATION_ORIGINAL_CHANGED")
    old_version = snapshot["version"]["id"]
    if version == old_version:
        raise ValueError("VISUAL_REMATERIALIZATION_NEW_VERSION_REQUIRED")
    if any(
        asset.get("materialization_input_sha256") != expected
        for asset in store.list_assets(version)
    ):
        raise ValueError("VISUAL_REMATERIALIZATION_TARGET_NOT_EMPTY")
    # This function is reachable only from an immutable before-enqueue plan. It preserves
    # the prior human history and perception revision; no new approval event is invented.
    document = rematerialize_canonical(snapshot, version)
    run = uuid.uuid4().hex
    store.ledger.put(
        "version",
        version,
        {
            "run": run,
            "stage": "materializing",
            "total_images": len(snapshot["assets"]),
            "source_version_id": old_version,
            "materialization_revision": REVISION,
        },
    )
    assets = []
    for old in snapshot["assets"]:
        check_cancelled()
        data = store.read_image(old_version, old)
        binding = store.save_image(version, data, old["locator"])
        if binding["id"] != old["id"]:
            raise ValueError("VISUAL_REMATERIALIZATION_ASSET_ID_MISMATCH")
        assets.append(
            {
                **copy.deepcopy(old),
                **binding,
                "materialization_revision": REVISION,
                "materialization_source_version_id": old_version,
                "materialization_input_sha256": expected,
                "stage": "completed" if old["status"] == "verified" else "excluded",
            }
        )
        store.ledger.upsert_asset(version, {**assets[-1], "ordinal": len(assets) - 1}, run=run)
    check_cancelled()
    state = store.ledger.get("version", version)
    if state.get("run") != run:
        raise ValueError("VISUAL_EXECUTION_SUPERSEDED")
    store.storage.write_bytes(
        "artifacts",
        store.manifest_key(version),
        json.dumps(
            {"version_id": version, "assets": assets}, ensure_ascii=False, sort_keys=True
        ).encode(),
    )
    store.ledger.put(
        "version",
        version,
        {
            "run": run,
            "stage": "completed",
            "total_images": len(assets),
            "coverage": snapshot["visual_processing"].get("coverage", {}),
            "materialization_revision": REVISION,
            "source_version_id": old_version,
            "completed_at": time.time(),
            "vision_model_calls": 0,
            "embedding_reindex_required": True,
        },
        expected=state["row_version"],
    )
    return replace(document, media_refs=tuple(assets))
