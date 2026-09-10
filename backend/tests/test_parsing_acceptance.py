from __future__ import annotations

import copy
import json

import pytest
from ragkb.application.acceptance_trace import capture_content, content_stage
from ragkb.domain.parsing_acceptance import evaluate
from ragkb.infrastructure.acceptance_lineage import overlay_review
from test_workspace_redesign import upload
from test_workspace_redesign import workspace as workspace


def test_standard_limits_total_distinct_pages_not_only_each_check():
    from pydantic import ValidationError
    from ragkb.domain.parsing_acceptance import ParsingStandard

    with pytest.raises(ValidationError, match="SELECT_UP_TO_20_ORIGINAL_PAGES"):
        ParsingStandard(
            key="too-many-pages",
            document_id="d",
            reference_version_id="v",
            checks=[
                {"id": f"P{i}", "label": "页面文字", "quote": "来源文字", "pages": [i]}
                for i in range(1, 22)
            ],
        )


def standard(client, runtime, space, quote="星云 X1 的保修期为三年。"):
    document = upload(client, runtime, space)
    payload = {
        "key": "原文件标准",
        "document_id": document["document_id"],
        "reference_version_id": document["document_version_id"],
        "original_checked": True,
        "note": "已对照上传前的原文件确认文字及条件",
        "checks": [{"id": "P1", "label": "保修期", "kind": "text", "pages": [1], "quote": quote}],
    }
    response = client.post(
        f"/api/spaces/{space}/acceptance/parsing/standards", json={"standard": payload}
    )
    assert response.status_code == 200, response.text
    return document, payload, response.json()


def run(client, space, document, saved, key="run"):
    return client.post(
        f"/api/spaces/{space}/acceptance/parsing/runs",
        headers={"Idempotency-Key": key},
        json={"standard_id": saved["id"], "version_id": document["document_version_id"]},
    )


def test_original_standards_inspect_staged_canonical_and_chunks_without_publishing(workspace):
    client, runtime, _, space = workspace
    document, spec, saved = standard(client, runtime, space)
    result = run(client, space, document, saved)
    assert result.status_code == 200, result.text
    data = result.json()["payload"]
    assert data["verdict"] == "passed", json.dumps(data, ensure_ascii=False)
    assert data["snapshot"]["parsed"] and data["snapshot"]["chunks"]
    assert data["standard"]["checks"] == spec["checks"]
    assert run(client, space, document, saved).json()["id"] == result.json()["id"]


def test_parser_output_cannot_be_used_as_its_own_gold_or_replace_missing_artifact(
    workspace, monkeypatch
):
    client, runtime, _, space = workspace
    document, _, saved = standard(client, runtime, space, "原文件必须保留的额外条件")
    result = run(client, space, document, saved).json()["payload"]
    assert result["verdict"] == "failed"
    assert result["rows"][0]["stages"]["parsed"]["status"] == "not_found"
    monkeypatch.setattr(runtime.repository, "list_local_content_lineage", lambda _: ())
    result = run(client, space, document, saved, "missing-artifact").json()["payload"]
    assert result["snapshot"]["parsed"] is None
    assert result["rows"][0]["stages"]["parsed"]["status"] == "unrecorded"


def test_standard_revision_keeps_prior_results_and_prevents_changed_gold_comparison(workspace):
    client, runtime, _, space = workspace
    document, spec, saved = standard(client, runtime, space)
    first = run(client, space, document, saved).json()
    spec["checks"][0]["quote"] = "星云 X1 支持上门维修服务。"
    updated = client.post(
        f"/api/spaces/{space}/acceptance/parsing/standards", json={"standard": spec, "revision": 1}
    )
    assert updated.status_code == 200
    second = run(client, space, document, updated.json(), "second").json()
    comparison = client.get(
        f"/api/spaces/{space}/acceptance/parsing/compare",
        params={"baseline": first["id"], "candidate": second["id"]},
    ).json()
    assert not comparison["comparable"]
    history = client.get(
        f"/api/spaces/{space}/acceptance/parsing/standards/{saved['id']}/revisions"
    ).json()
    assert (
        len(history) == 2
        and history[1]["payload"]["checks"][0]["quote"] != spec["checks"][0]["quote"]
    )
    assert (
        client.post(
            f"/api/spaces/{space}/acceptance/parsing/standards",
            json={"standard": spec, "revision": 1},
        ).status_code
        == 412
    )


def test_parsing_access_is_scoped_to_document_and_current_manager(workspace):
    client, runtime, _, space = workspace
    document, _, saved = standard(client, runtime, space)
    other = client.post("/api/spaces", json={"name": "隔离库"}).json()["id"]
    assert (
        client.get(
            f"/api/spaces/{other}/acceptance/parsing/versions/{document['document_version_id']}"
        ).status_code
        == 404
    )
    assert run(client, other, document, saved).status_code == 404
    second = upload(client, runtime, space, name="new.md", body="# 新原文\n已修改条款")
    assert run(client, space, second, saved).status_code == 422


def test_unconfirmed_original_standard_cannot_run(workspace):
    client, runtime, _, space = workspace
    document, spec, saved = standard(client, runtime, space)
    spec["original_checked"] = False
    client.post(
        f"/api/spaces/{space}/acceptance/parsing/standards", json={"standard": spec, "revision": 1}
    )
    assert run(client, space, document, saved).status_code == 422


def test_table_row_check_does_not_join_values_across_distinct_rows():
    check = {
        "id": "row",
        "label": "对象与数值",
        "kind": "table_row",
        "pages": [2],
        "quote": "A 200",
    }
    rows = [
        {
            "id": "table",
            "text": "<table><tr><td>A</td></tr><tr><td>200</td></tr></table>",
            "locator": {"page": 2},
        }
    ]
    result = evaluate([check], {"parsed": rows, "chunks": rows})
    assert result[0]["stages"]["parsed"]["status"] == "not_found"


@pytest.mark.parametrize(
    "kind,quote,wrong",
    [
        ("list_item", "10. 归档记录", "4. 归档记录"),
        ("condition", "满30天且已激活，非定制产品可以退货", "满30天已激活，定制产品可以退货"),
        ("text", "温度不得低于-5℃", "温度不得低于5℃"),
    ],
)
def test_checks_preserve_numbers_conditions_negations(kind, quote, wrong):
    check = {"id": "P", "label": "原文要求", "kind": kind, "pages": [1], "quote": quote}

    def snapshot(text):
        row = {"id": "node", "text": text, "locator": {"page": 1}}
        return {"parsed": [row], "chunks": [row]}

    assert evaluate([check], snapshot(wrong))[0]["stages"]["parsed"]["status"] == "not_found"
    assert evaluate([check], snapshot(quote))[0]["stages"]["chunks"]["status"] == "found"


def test_trace_is_opt_in_isolated_and_captures_actual_input_not_display_evidence():
    content_stage("model_input", [{"text": "outside"}])
    with capture_content() as outer:
        content_stage("model_input", [{"text": "sent-to-model"}])
        with capture_content() as inner:
            content_stage("model_input", [{"text": "other-question"}])
        assert outer["model_input"]["rows"][0]["text"] == "sent-to-model"
        content_stage("model_input", [{"text": "repair-input"}])
        assert outer["model_input"]["rows"][0]["text"] == "sent-to-model"
        assert outer["repair_input_calls"] == 1
    assert inner["model_input"]["rows"][0]["text"] == "other-question"


def test_missing_model_input_is_unrecorded_not_inferred_from_retrieval(workspace):
    from test_acceptance_v2 import prepare

    client, runtime, space, binding, case = prepare(workspace, 1)
    service = client.app.state.acceptance_service
    step = {
        "result": {"answer": binding["quote"]},
        "diagnostics": {
            "content_trace": {
                "retrieval": {
                    "recorded": True,
                    "rows": [{"document_id": binding["document_id"], "text": binding["quote"]}],
                }
            }
        },
    }
    report = service.lineage.report(
        runtime.authenticator.authenticate(None), space, case["payload"], step
    )
    row = report["rows"][0]
    assert row["stages"]["retrieval"]["status"] == "found"
    assert row["stages"]["model_input"]["status"] == "unrecorded"
    assert row["stages"]["original"]["status"] == "unassessed"
    copy_report = overlay_review(
        copy.deepcopy(report), [{"point_id": "P0", "status": "covered", "origin": "human"}]
    )
    assert copy_report["rows"][0]["stages"]["final"]["semantic_status"] == "covered"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("| 地区 | 标准 |\n| A | 200 |", "found"),
        ("| A |\n| 200 |", "not_found"),
        ("<tr><td>A</td></tr><tr><td>200</td></tr>", "not_found"),
        ("| A | -200 |", "not_found"),
    ],
)
def test_markdown_and_partial_html_tables_preserve_row_relationships(text, expected):
    check = {"id": "P", "label": "地区费用", "kind": "table_row", "pages": [1], "quote": "A 200"}
    rows = [{"id": "table", "text": text, "locator": {"page": 1}}]
    result = evaluate([check], {"parsed": rows, "chunks": rows})
    assert result[0]["stages"]["chunks"]["status"] == expected


def test_comparison_identifies_chunk_loss_and_recovery_with_frozen_gold(workspace, monkeypatch):
    client, runtime, _, space = workspace
    document, _, saved = standard(client, runtime, space)
    service = client.app.state.acceptance_service
    snapshot = service.parsing.snapshot

    def missing(*args):
        value = snapshot(*args)
        value["chunks"] = []
        return value

    with monkeypatch.context() as patch:
        patch.setattr(service.parsing, "snapshot", missing)
        old = run(client, space, document, saved, "missing-chunks").json()
    new = run(client, space, document, saved, "restored-chunks").json()
    compared = client.get(
        f"/api/spaces/{space}/acceptance/parsing/compare",
        params={"baseline": old["id"], "candidate": new["id"]},
    ).json()
    assert compared["comparable"]
    assert [(r["stage"], r["change"]) for r in compared["rows"]] == [
        ("parsed", "unchanged"),
        ("chunks", "improved"),
    ]
    assert compared["timing_scope"] == "local_validation_only"


def test_processing_snapshot_is_unknown_until_index_and_quality_are_ready(workspace, monkeypatch):
    from ragkb.domain.uploads import ResourceNotFoundError

    client, runtime, _, space = workspace
    document, _, saved = standard(client, runtime, space)

    def pending(_):
        raise ResourceNotFoundError("quality-not-finished")

    monkeypatch.setattr(runtime.repository, "get_quality_report", pending)
    value = run(client, space, document, saved).json()["payload"]
    assert value["verdict"] == "incomplete"
    assert value["rows"][0]["stages"]["chunks"]["status"] == "unrecorded"
    assert value["rows"][0]["stages"]["parsed"]["status"] == "found"
