"""Resolve cited graph coordinates by verified fact identity, never answer text similarity."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ragkb.domain.graph_facts import GraphFact, graph_facts
from ragkb.domain.rag import AskResult, Evidence
from ragkb.domain.visuals import VisualExtraction


def cited_graph_targets(
    evidence: Evidence, asset: dict[str, Any], result: AskResult | None
) -> list[dict[str, Any]]:
    """Only the caller's current, authorized, actually cited evidence can expose targets.

    Old runs without a per-fact usage receipt receive explicitly labelled source candidates;
    they are not automatically highlighted as facts used in the displayed answer.
    """
    if (
        not evidence.authorized
        or not evidence.current_version
        or not result
        or not result.verified
        or not result.answer
        or evidence.evidence_id not in {citation.evidence_id for citation in result.citations}
        or asset.get("status") != "verified"
        or asset.get("id") not in evidence.locator.get("visual_asset_ids", [])
        or not asset.get("extraction")
    ):
        return []
    extraction = VisualExtraction.model_validate(asset["extraction"])
    if extraction.issues():
        return []
    has_receipt = "used_visual_fact_ids" in evidence.locator
    receipt = evidence.locator.get("used_visual_fact_ids", [])
    if has_receipt and (
        not isinstance(receipt, list) or any(not isinstance(i, str) for i in receipt)
    ):
        return []
    used = set(receipt)
    current: dict[int, dict[str, GraphFact]] = {}
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in evidence.locator.get("visual_facts", []):
        if not isinstance(row, dict) or row.get("asset_id") != asset["id"]:
            continue
        index = row.get("graph_index")
        identity = row.get("fact_id")
        if (
            type(index) is not int
            or not 0 <= index < len(extraction.graphs)
            or not isinstance(identity, str)
            or identity in seen
            or (has_receipt and identity not in used)
        ):
            continue
        if index not in current:
            current[index] = {
                f.fact_id: f
                for f in graph_facts(
                    extraction.graphs[index],
                    scope=f"{evidence.document_version_id}:{asset['id']}:{index}",
                    verified=True,
                )
            }
        fact = current[index].get(identity)
        if fact is None or fact.bbox is None or fact.bbox_basis not in {"ocr", "human"}:
            continue
        expected = asdict(fact)
        if any(row.get(key) != value for key, value in expected.items() if key != "bbox"):
            continue
        bbox = row.get("bbox")
        if not isinstance(bbox, (list, tuple)) or tuple(bbox) != fact.bbox:
            continue
        seen.add(identity)
        targets.append(
            {
                "fact_id": identity,
                "kind": fact.kind,
                "element_id": fact.element_id,
                "graph_index": index,
                "text": fact.text,
                "bbox": list(fact.bbox),
                "bbox_basis": fact.bbox_basis,
                "usage": "answer" if has_receipt else "citation_candidate",
            }
        )
        if len(targets) >= 64:
            break
    return targets
