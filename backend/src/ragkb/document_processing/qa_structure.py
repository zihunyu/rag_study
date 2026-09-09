"""Rebuildable original-source anchors. Never infer authoritative heading levels."""

import hashlib
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from ragkb.domain.answer_conditions import condition_quotes
from ragkb.domain.documents import CanonicalNode, NodeType
from ragkb.domain.source_references import references

REVISION = "qa-source-anchors-v1"


def condition_anchors(text: str) -> dict[str, Any]:
    return {
        "condition_anchors": {
            "revision": REVISION,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "quotes": condition_quotes(text),
        }
    }


def prepare_nodes(nodes: Sequence[CanonicalNode]) -> tuple[CanonicalNode, ...]:
    nodes = tuple(nodes)
    levels = {n.metadata.get("heading_level", 1) for n in nodes if n.node_type is NodeType.HEADING}
    uncertain = len(levels) == 1 and sum(n.node_type is NodeType.HEADING for n in nodes) > 1
    heading = None
    result = []
    ref_targets: dict[str, list[str]] = {}
    for node in nodes:
        if node.node_type in {NodeType.IMAGE, NodeType.TABLE}:
            for identity, _ in references(
                node.metadata.get("visual_caption", "") or node.display_text
            ):
                ref_targets.setdefault(identity, []).append(node.node_id)
    for index, node in enumerate(nodes):
        if node.node_type is NodeType.HEADING:
            heading = node.node_id
        links = list(
            dict.fromkeys(
                target
                for identity, _ in references(node.display_text)
                for target in ref_targets.get(identity, [])
                if target != node.node_id
            )
        )
        result.append(
            replace(
                node,
                metadata={
                    **node.metadata,
                    **condition_anchors(node.display_text),
                    "qa_structure": {
                        "revision": REVISION,
                        "node_id": node.node_id,
                        "ordinal": index,
                        "content_sha256": hashlib.sha256(node.original_text.encode()).hexdigest(),
                        "previous_node_id": nodes[index - 1].node_id if index else None,
                        "next_node_id": nodes[index + 1].node_id
                        if index + 1 < len(nodes)
                        else None,
                        "heading_node_id": heading,
                        "heading_boundary_uncertain": uncertain,
                        "explicit_reference_node_ids": links,
                        # Keep adjacent source IDs for tables/footnotes, including cross-page
                        # neighbours. They are context candidates, not invented continuations.
                        "context_node_ids": [
                            n.node_id for n in nodes[max(0, index - 1) : index + 2]
                        ],
                        "visual_asset_ids": node.metadata.get("visual_asset_ids", []),
                        "visual_gap": node.node_type is NodeType.IMAGE
                        and not node.metadata.get("visual_asset_ids"),
                    },
                },
            )
        )
    return tuple(result)
