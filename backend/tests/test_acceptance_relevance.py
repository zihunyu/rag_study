from __future__ import annotations

import pytest
from ragkb.domain.acceptance import AcceptanceCase
from ragkb.domain.acceptance_points import RELEVANCE_POINT_ID, criteria_for
from ragkb.domain.errors import InvalidProviderResponse
from test_acceptance import execute
from test_acceptance_v2 import prepare
from test_workspace_redesign import workspace as workspace


def test_relevance_is_separate_from_completeness_and_cannot_shadow_user_points():
    assert not criteria_for({"criteria": [], "check_relevance": False})
    points = criteria_for(
        {
            "criteria": [{"id": "P1", "kind": "required", "text": "十项", "sources": []}],
            "check_relevance": True,
        }
    )
    assert [p["kind"] for p in points] == ["required", "relevance"]
    with pytest.raises(ValueError, match="RESERVED_POINT_ID"):
        AcceptanceCase(
            key="a", question="问题", criteria=[{"id": RELEVANCE_POINT_ID, "text": "覆盖保留编号"}]
        )


@pytest.mark.parametrize(
    "status,quote,valid",
    [
        ("incorrect", "", False),
        ("incorrect", "不存在的内容", False),
        ("incorrect", "退货须配件齐全。", True),
        ("covered", "", True),
        ("missing", "", False),
        ("pending_review", "", True),
    ],
)
def test_relevance_requires_real_answer_witness_for_failures(
    workspace, monkeypatch, status, quote, valid
):
    client, _, _, _ = workspace
    assistance = client.app.state.acceptance_service.assistance
    monkeypatch.setattr(
        assistance.model,
        "call",
        lambda *_: {
            "points": [
                {
                    "point_id": RELEVANCE_POINT_ID,
                    "status": status,
                    "answer_quote": quote,
                    "note": "问题询问操作清单，退货条件属于另一事项。",
                }
            ]
        },
    )
    point = criteria_for({"check_relevance": True})
    if valid:
        assert (
            assistance.review_batch("操作清单？", "退货须配件齐全。", point)[0]["status"] == status
        )
    else:
        with pytest.raises(InvalidProviderResponse, match="POINT_REVIEW_WITNESS_INVALID"):
            assistance.review_batch("操作清单？", "退货须配件齐全。", point)


def test_relevance_failure_is_persisted_reviewable_and_prevents_acceptance(workspace, monkeypatch):
    client, runtime, space, _, case = prepare(workspace, count=1)
    spec = {**case["payload"], "check_relevance": True}
    response = client.put(
        f"/api/spaces/{space}/acceptance/cases/{case['id']}",
        json={"case": spec, "revision": case["revision"]},
    )
    assert response.status_code == 200, response.text
    service = client.app.state.acceptance_service
    captured = []

    def assess(question, answer, points):
        captured.extend(points)
        return [
            {
                "point_id": p["id"],
                "status": "incorrect" if p["kind"] == "relevance" else "covered",
                "answer_quote": answer,
                "note": "复核上下文",
                "origin": "model",
            }
            for p in points
        ]

    monkeypatch.setattr(service.assistance, "review_batch", assess)
    run = client.post(
        f"/api/spaces/{space}/acceptance/runs",
        headers={"Idempotency-Key": "relevance"},
        json={
            "name": "相关性",
            "case_ids": [case["id"]],
            "review_mode": "assisted",
            "call_limit": 10,
        },
    ).json()
    detail = execute(client, runtime, space, run)
    attempt = detail["attempts"][0]
    assert len(captured) == 2
    assert attempt["verdict"] == "failed"
    assert attempt["point_results"][-1]["point_id"] == RELEVANCE_POINT_ID
    # Relevance is not a claim that every gold source quote must occur in the answer.
    assert all(
        r["point_id"] != RELEVANCE_POINT_ID for r in attempt["payload"]["content_lineage"]["rows"]
    )
    path = f"/api/spaces/{space}/acceptance/runs/{run['id']}/attempts/{attempt['id']}/reviews"
    points = [
        {k: p[k] for k in ("point_id", "status", "answer_quote", "note")}
        for p in attempt["point_results"]
    ]
    assert (
        client.post(
            path,
            json={
                "verdict": "passed",
                "sources_checked": True,
                "note": "检查",
                "point_reviews": points,
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            path,
            json={
                "verdict": "failed",
                "sources_checked": True,
                "note": "确认失败",
                "point_reviews": points,
            },
        ).status_code
        == 200
    )
