"""Only current, actually cited and source-anchored graph facts may locate an answer."""

from __future__ import annotations

import copy
from dataclasses import asdict, replace

import pytest
from ragkb.domain.graph_facts import graph_facts
from ragkb.domain.rag import AnswerStatus, AskResult, Citation, Evidence
from ragkb.infrastructure.graph_citations import cited_graph_targets
from test_graph_identity_and_queries import extraction, fixture


def source():
    graph = fixture()
    for index, edge in enumerate(graph.edges):
        edge.bbox = [0.05, 0.1 + index * 0.1, 0.6, 0.2 + index * 0.1]
        edge.bbox_basis = "human"
    asset = {
        "id": "diagram",
        "status": "verified",
        "origin": "human_review",
        "extraction": extraction(graph).model_dump(),
    }
    rows = [
        {**asdict(fact), "asset_id": "diagram", "graph_index": 0}
        for fact in graph_facts(graph, scope="v:diagram:0", verified=True)
    ]
    chosen = next(row["fact_id"] for row in rows if row["kind"] == "edge")
    evidence = Evidence(
        "E1",
        "chunk",
        "document",
        "v",
        "网关的连接关系",
        {"visual_asset_ids": ["diagram"], "visual_facts": rows, "used_visual_fact_ids": [chosen]},
        0,
        0,
        0,
        1,
        True,
        True,
    )
    answer = AskResult(
        "run",
        AnswerStatus.ANSWERED,
        "网关的连接关系。[E1]",
        (Citation("E1", "/source", {}),),
        (evidence,),
        (),
        True,
    )
    return evidence, asset, answer, chosen


def test_source_focus_is_selected_by_actual_fact_receipt_not_matching_answer_words():
    evidence, asset, answer, chosen = source()
    targets = cited_graph_targets(evidence, asset, answer)
    assert len(targets) == 1 and targets[0]["fact_id"] == chosen
    assert targets[0]["usage"] == "answer" and targets[0]["bbox_basis"] == "human"
    # Text can be a natural paraphrase. No string matching guesses its arrow location.
    assert (
        cited_graph_targets(evidence, asset, replace(answer, answer="上述关系成立。[E1]"))
        == targets
    )


def test_old_history_without_fact_receipt_only_exposes_explicit_candidates():
    evidence, asset, answer, _ = source()
    locator = dict(evidence.locator)
    del locator["used_visual_fact_ids"]
    targets = cited_graph_targets(replace(evidence, locator=locator), asset, answer)
    assert len(targets) == 4
    assert all(target["usage"] == "citation_candidate" for target in targets)
    locator["used_visual_fact_ids"] = []
    assert not cited_graph_targets(replace(evidence, locator=locator), asset, answer)


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "asset",
        "graph_index",
        "fact_scope",
        "text",
        "direction",
        "box",
        "model_box",
        "excluded",
        "pending",
        "unscoped_uncertainty",
    ],
)
def test_wrong_scope_or_changed_unapproved_fact_never_supplies_a_focus_region(change):
    evidence, asset, answer, chosen = source()
    rows = copy.deepcopy(evidence.locator["visual_facts"])
    row = next(row for row in rows if row["fact_id"] == chosen)
    if change == "version":
        evidence = replace(evidence, document_version_id="different-version")
    elif change == "asset":
        row["asset_id"] = "another-image"
    elif change == "graph_index":
        row["graph_index"] = 1
    elif change == "fact_scope":
        row["fact_id"] = "GF-foreign-version"
        evidence = replace(
            evidence, locator={**evidence.locator, "used_visual_fact_ids": [row["fact_id"]]}
        )
    elif change == "text":
        row["text"] = "网关连接测试区"
    elif change == "direction":
        row["direction"] = "none"
    elif change == "box":
        row["bbox"] = [0.6, 0.6, 0.9, 0.9]
    elif change == "model_box":
        row["bbox_basis"] = "unverified"
        asset["extraction"]["graphs"][0]["edges"][0]["bbox_basis"] = "unverified"
    elif change == "unscoped_uncertainty":
        asset["extraction"]["graphs"][0]["uncertainties"] = ["起点尚不明确"]
    else:
        asset["extraction"]["graphs"][0]["edges"][0]["review_status"] = change
    evidence = replace(evidence, locator={**evidence.locator, "visual_facts": rows})
    assert not cited_graph_targets(evidence, asset, answer)


@pytest.mark.parametrize(
    "change",
    [
        "unauthorized",
        "old_version",
        "uncited",
        "unverified_answer",
        "unverified_asset",
        "no_answer",
    ],
)
def test_fact_focus_requires_actual_citation_and_current_authorized_verified_sources(change):
    evidence, asset, answer, _ = source()
    if change == "unauthorized":
        evidence = replace(evidence, authorized=False)
    elif change == "old_version":
        evidence = replace(evidence, current_version=False)
    elif change == "uncited":
        answer = replace(answer, citations=())
    elif change == "unverified_answer":
        answer = replace(answer, verified=False)
    elif change == "no_answer":
        answer = replace(answer, answer=None)
    else:
        asset["status"] = "needs_review"
    assert not cited_graph_targets(evidence, asset, answer)
