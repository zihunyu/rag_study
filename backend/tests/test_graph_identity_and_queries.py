"""Human-addressed endpoint gold and graph QA regressions without external services."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from ragkb.domain.graph_facts import accepted_graph_projection, graph_facts, query_graph
from ragkb.domain.graph_identity import match_graphs
from ragkb.domain.visual_comparison import compare_readings
from ragkb.domain.visual_evaluation import evaluate_pair
from ragkb.domain.visual_graph import Graph
from ragkb.domain.visuals import VisualExtraction, reviewed_extraction


def fixture(name="architecture") -> Graph:
    path = Path(__file__).parent / "fixtures/visual_evaluation/complex_graphs.json"
    return Graph.model_validate(json.loads(path.read_text(encoding="utf-8"))[name])


def extraction(graph: Graph) -> VisualExtraction:
    return VisualExtraction(
        kind="diagram",
        title="系统关系",
        transcription="",
        description="",
        graphs=[graph],
        tables=[],
        uncertainties=[],
    )


def rename_and_reorder(graph: Graph) -> Graph:
    graph = graph.model_copy(deep=True)
    aliases = {
        key: f"opaque-{i}"
        for i, key in enumerate([g.id for g in graph.groups] + [n.id for n in graph.nodes])
    }
    for group in graph.groups:
        group.id = aliases[group.id]
        group.parent = aliases[group.parent] if group.parent else None
    for node in graph.nodes:
        node.id = aliases[node.id]
        node.group = aliases[node.group] if node.group else None
    for edge in graph.edges:
        edge.source, edge.target = aliases[edge.source], aliases[edge.target]
    graph.groups.reverse()
    graph.nodes.reverse()
    graph.edges.reverse()
    return graph


def test_repeated_names_are_valid_when_scopes_and_topology_match_with_different_ids():
    gold = fixture()
    second = rename_and_reorder(gold)
    assert compare_readings(extraction(gold), extraction(second)) == []
    assert match_graphs(gold, second).mapping is not None
    metrics = evaluate_pair(extraction(gold), extraction(second))
    assert metrics["nodes_correct"] == 5 and metrics["edges_correct"] == 4
    assert metrics["identity_ambiguous"] == 0


def test_graph_with_footnote_kind_and_question_mark_typography_still_require_same_structure():
    first = extraction(fixture("flow"))
    second = first.model_copy(deep=True)
    second.kind = "mixed"
    second.graphs[0].nodes[1].label = "检测成功?"
    assert compare_readings(first, second) == []
    metrics = evaluate_pair(first, second)
    assert metrics["nodes_correct"] == metrics["edges_correct"] == 5
    assert metrics["nodes_exact"] == 4 and metrics["edges_exact"] < 5
    assert not metrics["kind_correct"]
    second.graphs[0].edges[1].label = "否"
    assert compare_readings(first, second)
    assert evaluate_pair(first, second)["edges_correct"] < 5
    second.graphs[0].edges[1].label = "是"
    second.graphs[0].nodes[1].label = "电压<24mW?"
    first.graphs[0].nodes[1].label = "电压>24MW？"
    assert compare_readings(first, second)
    assert evaluate_pair(first, second)["nodes_correct"] < 5


@pytest.mark.parametrize(
    "mutation",
    ["anonymous_endpoint", "same_name_instance", "containment", "condition", "direction", "shape"],
)
def test_actual_wrong_relationship_never_passes_identity_comparison(mutation):
    gold = fixture()
    wrong = gold.model_copy(deep=True)
    if mutation == "anonymous_endpoint":
        wrong.edges[0].target = "t-box"
    elif mutation == "same_name_instance":
        wrong.edges[1].target = "t-db"
    elif mutation == "containment":
        wrong.groups[2].parent = "test"
    elif mutation == "condition":
        wrong.edges[3].condition = "未经授权"
    elif mutation == "direction":
        wrong.edges[0].direction = "none"
    else:
        wrong.nodes[1].shape = "diamond"
    assert compare_readings(extraction(gold), extraction(wrong))
    assert evaluate_pair(extraction(gold), extraction(wrong))["edges_correct"] < 4


def test_independent_gold_scorer_does_not_reuse_online_comparison(monkeypatch):
    import ragkb.domain.visual_comparison as online

    monkeypatch.setattr(online, "_edges", lambda value: {})
    gold = fixture()
    wrong = gold.model_copy(deep=True)
    wrong.edges[0].target = "t-box"
    assert evaluate_pair(extraction(gold), extraction(wrong))["edges_correct"] == 3


def test_nested_unnamed_container_and_repeated_node_keep_readable_identity():
    text = extraction(fixture()).retrieval_text()
    assert "网关 双向连接 生产区 / 未命名区域（包含：NACOS、数据库）" in text
    assert "生产区 / 未命名区域（包含：NACOS、数据库） / 数据库" in text
    assert "测试区 / 未命名区域（包含：NACOS、数据库） / 数据库" in text
    assert "演练已授权" in text
    assert "指向 ；" not in text


def test_branch_facts_preserve_question_and_yes_no_premises():
    graph = fixture("flow")
    result = query_graph(graph, scope="v:a:0", verified=True, source_ids=["check"], mode="branches")
    assert len(result.facts) == 3  # Includes the visible repair -> recheck loop.
    assert {fact.condition for fact in result.facts if fact.condition} == {
        "判断：检测成功？；分支：是",
        "判断：检测成功？；分支：否",
    }
    assert "判断：检测成功？；分支：否" in extraction(graph).retrieval_text()


@pytest.mark.parametrize("label", ["oauth2\n认证\nJWT", "能力金字塔", "核心能力"])
def test_architecture_diamond_is_geometry_and_does_not_invent_a_decision(label):
    graph = fixture("flow")
    graph.nodes = graph.nodes[1:3]
    graph.nodes[0].label = label
    graph.nodes[1].label = "Nginx"
    graph.edges = [graph.edges[1]]
    graph.edges[0].label = ""
    text = extraction(graph).retrieval_text()
    assert "指向 Nginx" in text
    assert "判断：" not in text and "分支：" not in text and "未标明条件" not in text
    result = query_graph(graph, scope="v:a:0", verified=True, source_ids=["check"], mode="branches")
    assert len(result.facts) == 1 and not result.gaps


@pytest.mark.parametrize("mode", ["direct", "branches", "paths"])
def test_printed_decision_without_branch_label_retains_an_explicit_coverage_gap(mode):
    graph = fixture("flow")
    graph.edges[1].label = ""
    result = query_graph(
        graph,
        scope="v:a:0",
        verified=True,
        source_ids=["check"],
        target_ids=["finish"],
        mode=mode,
    )
    assert any("判断：检测成功？；分支：原图未标明条件" in f.condition for f in result.facts)
    assert any("未标明适用条件" in gap for gap in result.gaps)


def test_action_success_output_preserves_visible_condition_without_inventing_decision_role():
    graph = fixture("flow")
    graph.nodes[1].label, graph.nodes[1].shape = "检测设备", "rect"
    graph.edges[1].label = "成功"
    facts = graph_facts(graph, scope="v:a:0", verified=True)
    branch = next(f for f in facts if f.element_id == "edge:1")
    assert branch.condition == "条件分支起点：检测设备；分支：成功"
    graph.edges[1].direction = "none"
    assert (
        next(
            f for f in graph_facts(graph, scope="v:a:0", verified=True) if f.element_id == "edge:1"
        ).condition
        == ""
    )


def test_branch_summary_from_single_entry_follows_downstream_decisions_and_reports_cutoff():
    graph = fixture("flow")
    complete = query_graph(
        graph, scope="v:a:0", verified=True, source_ids=["start"], mode="branches"
    )
    assert len(complete.facts) == 4 and not complete.truncated
    assert any(f.target_id == "repair" and "分支：否" in f.condition for f in complete.facts)
    assert any(f.source_id == "repair" and f.target_id == "check" for f in complete.facts)
    assert all(f.direction != "none" for f in complete.facts)
    limited = query_graph(
        graph, scope="v:a:0", verified=True, source_ids=["start"], mode="branches", max_hops=1
    )
    assert len(limited.facts) == 1 and limited.truncated


def test_paths_follow_explicit_arrows_and_keep_conditions_with_loops_bounded():
    graph = fixture("flow")
    result = query_graph(
        graph,
        scope="v:a:0",
        verified=True,
        source_ids=["start"],
        target_ids=["repair"],
        mode="paths",
    )
    assert len(result.paths) == 1 and len(result.paths[0]) == 2
    assert any("分支：否" in fact.condition for fact in result.facts)
    assert not any(fact.source_id == "note" for fact in result.facts)
    assert not query_graph(
        graph,
        scope="v:a:0",
        verified=True,
        source_ids=["note"],
        target_ids=["repair"],
        mode="paths",
    ).paths
    bounded = query_graph(
        graph,
        scope="v:a:0",
        verified=True,
        source_ids=["start"],
        target_ids=["finish"],
        mode="paths",
        max_hops=1,
    )
    assert not bounded.paths and bounded.truncated


def test_partial_review_keeps_only_independent_confirmed_facts_and_reports_gaps():
    graph = fixture("flow")
    graph.nodes[3].review_status = "excluded"
    result = query_graph(
        graph,
        scope="v:a:0",
        verified=True,
        source_ids=["start"],
        target_ids=["repair"],
        mode="paths",
    )
    assert not result.paths and result.gaps
    assert all(f.source_id != "repair" and f.target_id != "repair" for f in result.facts)
    assert query_graph(
        graph,
        scope="v:a:0",
        verified=True,
        source_ids=["start"],
        target_ids=["finish"],
        mode="paths",
    ).paths
    view = extraction(graph).model_copy(
        update={
            "description": "更换设备后结束",
            "body_text": "更换设备",
            "transcription": "更换设备",
        }
    )
    assert "更换设备" not in view.retrieval_text()
    edited = reviewed_extraction(extraction(fixture("flow")), view)
    assert not edited.description and not edited.transcription and not edited.body_text


def test_excluded_parent_removes_descendant_facts_and_incident_edges():
    graph = fixture()
    graph.groups[0].review_status = "pending"
    projection = accepted_graph_projection(graph)
    assert {node.id for node in projection.nodes} == {"gateway", "t-nacos", "t-db"}
    assert len(projection.edges) == 1
    assert "生产区" not in extraction(graph).retrieval_text()


def test_unscoped_uncertainty_never_becomes_automatic_partial_approval():
    graph = fixture()
    graph.uncertainties = ["箭头方向无法确定"]
    for node in graph.nodes:
        node.review_status = "confirmed"
    assert not graph_facts(graph, scope="v:a:0", verified=True)
    assert not graph_facts(fixture(), scope="v:a:0", verified=False)


def test_fact_identity_is_stable_for_one_version_and_never_crosses_versions():
    graph = fixture()
    first = graph_facts(graph, scope="v1:a:0", verified=True)
    assert first == graph_facts(graph, scope="v1:a:0", verified=True)
    second = graph_facts(graph, scope="v2:a:0", verified=True)
    assert not {f.fact_id for f in first} & {f.fact_id for f in second}
    assert len({f.fact_id for f in first}) == len(first)


def test_reliable_positions_disambiguate_same_name_instances_but_model_guesses_do_not():
    graph = fixture("flow")
    graph.nodes[0].label = graph.nodes[2].label = "端点"
    graph.nodes[0].bbox = [0.1, 0.1, 0.3, 0.3]
    graph.nodes[2].bbox = [0.7, 0.7, 0.9, 0.9]
    graph.nodes[0].bbox_basis = graph.nodes[2].bbox_basis = "human"
    wrong = graph.model_copy(deep=True)
    wrong.nodes[0].bbox, wrong.nodes[2].bbox = wrong.nodes[2].bbox, wrong.nodes[0].bbox
    assert match_graphs(graph, wrong).mapping is None
    assert evaluate_pair(extraction(graph), extraction(wrong))["edges_correct"] < len(graph.edges)
    wrong.nodes[0].bbox_basis = wrong.nodes[2].bbox_basis = "unverified"
    assert match_graphs(graph, wrong).mapping is not None


def test_equal_symmetric_instances_are_not_rejected_merely_for_a_duplicate_label():
    graph = fixture("flow")
    graph.nodes = [graph.nodes[0], graph.nodes[2]]
    graph.nodes[0].label = graph.nodes[1].label = "NACOS"
    graph.edges = []
    assert compare_readings(extraction(graph), extraction(rename_and_reorder(graph))) == []
    metrics = evaluate_pair(extraction(graph), extraction(graph))
    assert metrics["identity_ambiguous"] == 2
    assert metrics["nodes_correct"] == 0  # Gold needs explicit instance anchors for scoring.


def test_unique_graph_content_and_missing_source_coordinates_are_scored_independently():
    gold = fixture("flow")
    for index, element in enumerate([*gold.nodes, *gold.edges]):
        element.bbox = [0.1, 0.05 * index, 0.4, 0.05 * index + 0.04]
        element.bbox_basis = "human"
    predicted = rename_and_reorder(gold)
    for element in [*predicted.nodes, *predicted.edges]:
        element.bbox = None
        element.bbox_basis = "unverified"
    candidate = extraction(predicted)
    candidate.kind = "mixed"
    metrics = evaluate_pair(extraction(gold), candidate)
    assert metrics["nodes_correct"] == metrics["nodes_expected"] == 5
    assert metrics["edges_correct"] == metrics["edges_expected"] == 5
    assert not metrics["kind_correct"]  # Raw classification remains an independent metric.
    assert metrics["regions_expected"] == metrics["regions_missing"] == 10
    assert metrics["regions_correct"] == metrics["regions_predicted"] == 0
    assert {detail["status"] for detail in metrics["region_details"]} == {"missing"}


def test_region_mismatch_does_not_erase_unique_content_and_estimates_keep_their_provenance():
    gold = fixture("flow")
    gold.nodes[0].bbox, gold.nodes[0].bbox_basis = [0.1, 0.1, 0.3, 0.3], "human"
    predicted = gold.model_copy(deep=True)
    predicted.nodes[0].bbox_basis = "unverified"
    metrics = evaluate_pair(extraction(gold), extraction(predicted))
    assert metrics["regions_correct"] == 1
    assert predicted.nodes[0].bbox_basis == "unverified"
    predicted.nodes[0].bbox = [0.7, 0.7, 0.9, 0.9]
    metrics = evaluate_pair(extraction(gold), extraction(predicted))
    assert metrics["nodes_correct"] == 5 and metrics["edges_correct"] == 5
    assert metrics["regions_mismatched"] == 1 and metrics["regions_correct"] == 0


def test_duplicate_instances_without_boxes_cannot_use_edges_to_guess_gold_identity():
    gold = fixture("flow")
    gold.nodes[0].label = gold.nodes[2].label = "端点"
    gold.nodes[0].bbox, gold.nodes[0].bbox_basis = [0.1, 0.1, 0.3, 0.3], "human"
    gold.nodes[2].bbox, gold.nodes[2].bbox_basis = [0.7, 0.7, 0.9, 0.9], "human"
    predicted = gold.model_copy(deep=True)
    for node in predicted.nodes:
        node.bbox, node.bbox_basis = None, "unverified"
    metrics = evaluate_pair(extraction(gold), extraction(predicted))
    assert metrics["nodes_correct"] == 3 and metrics["identity_ambiguous"] == 2
    assert metrics["edges_correct"] < metrics["edges_expected"]
    assert metrics["regions_missing"] == 2
    assert {detail["status"] for detail in metrics["region_details"]} == {"identity_unresolved"}


def test_excluding_first_instance_keeps_surviving_instance_number_and_fact_id():
    graph = fixture("flow")
    graph.nodes = [graph.nodes[0], graph.nodes[2]]
    graph.nodes[0].label = graph.nodes[1].label = "NACOS"
    graph.edges = []
    before = graph_facts(graph, scope="v:a:0", verified=True)[1]
    graph.nodes[0].review_status = "excluded"
    after = graph_facts(graph, scope="v:a:0", verified=True)[0]
    assert before.fact_id == after.fact_id
    assert before.subject == after.subject == "NACOS〔实例 2〕"


def test_comparison_bound_is_inconclusive_instead_of_success():
    graph = fixture()
    result = match_graphs(graph, rename_and_reorder(graph), max_steps=0)
    assert result.mapping is None and result.exhausted


def test_dense_unreachable_path_search_bounds_queued_paths_and_returns_explicit_gap():
    graph = fixture("flow")
    graph.nodes = [
        graph.nodes[0].model_copy(update={"id": str(i), "label": f"组件{i}"}) for i in range(20)
    ]
    edge = graph.edges[0]
    graph.edges = [
        edge.model_copy(update={"source": str(a), "target": str(b)})
        for a in range(20)
        for b in range(20)
        if a != b
    ]
    result = query_graph(
        graph,
        scope="v:a:0",
        verified=True,
        source_ids=["0"],
        target_ids=["missing"],
        mode="paths",
        max_hops=12,
    )
    assert not result.paths and result.gaps and result.truncated


def test_non_numeric_table_conditions_are_checked_in_both_directions():
    from ragkb.domain.visuals import VisualTable

    table = VisualTable.model_validate(
        {
            "title": "维修条件",
            "rows": 1,
            "columns": 1,
            "cells": [{"row": 0, "column": 0, "rowspan": 1, "colspan": 1, "text": "免费维修"}],
            "notes": ["仅城区", "不含进水"],
        }
    )
    first = VisualExtraction(
        kind="table",
        title="",
        transcription="",
        description="",
        graphs=[],
        tables=[table],
        uncertainties=[],
    )
    second = first.model_copy(deep=True)
    second.tables[0].notes = ["仅城区"]
    assert compare_readings(first, second)
    assert compare_readings(second, first)


@pytest.mark.parametrize(
    "bbox", [[0.5, 0.2, 0.2, 0.5], [-0.1, 0.0, 0.2, 0.5], [0.0, 0.0, 1.1, 1.0]]
)
def test_source_region_validation_rejects_invalid_boxes(bbox):
    raw = fixture().model_dump()
    raw["nodes"][0]["bbox"] = bbox
    with pytest.raises(ValidationError, match="VISUAL_GRAPH_INVALID_SOURCE_REGION"):
        Graph.model_validate(raw)
