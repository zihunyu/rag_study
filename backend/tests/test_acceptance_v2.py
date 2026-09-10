from __future__ import annotations

import pytest
from ragkb.domain.acceptance import AcceptanceCase
from ragkb.domain.acceptance_points import compare_points, list_check_results
from ragkb.domain.errors import InvalidProviderResponse
from test_acceptance import execute, make_run
from test_workspace_redesign import publish, upload
from test_workspace_redesign import workspace as workspace


def prepare(workspace, count=2):
    client, runtime, _, space = workspace
    document = upload(client, runtime, space)
    publish(client, document["document_version_id"])
    page = client.get(f"/api/spaces/{space}/acceptance/sources/{document['document_id']}")
    assert page.status_code == 200, page.text
    source = page.json()["items"][0]
    binding = {k: source[k] for k in ("document_id", "version_id", "chunk_id")}
    binding["quote"] = source["text"]
    spec = AcceptanceCase.model_validate(
        {
            "key": "V2",
            "question": "星云 X1 的保修期是多久？",
            "criteria": [
                {"id": f"P{i}", "text": f"核对原文要点{i}", "sources": [binding]}
                for i in range(count)
            ],
            "source_notes": "已对照选定原文",
            "review_state": "confirmed",
        }
    ).model_dump(mode="json")
    saved = client.post(f"/api/spaces/{space}/acceptance/cases", json={"case": spec})
    assert saved.status_code == 200, saved.text
    return client, runtime, space, binding, saved.json()


def test_list_count_rejects_four_of_ten_even_when_qa_claims_verified():
    spec = {"list_checks": {"expected_count": 10, "sequential": True, "no_duplicates": True}}
    checks = list_check_results(spec, "\n".join(f"{i}. 独立条款{i} [E1]" for i in range(1, 5)))
    assert not checks[0]["passed"] and "实际 4" in checks[0]["detail"]
    duplicate = list_check_results(spec, "\n".join(f"{i}. 同一条款 [E{i}]" for i in range(1, 11)))
    assert duplicate[0]["passed"] and not duplicate[2]["passed"]
    mixed = list_check_results(spec, "第一项\n第二项\n第三项\n(4) 第四项")
    assert not mixed[0]["passed"] and not mixed[1]["passed"]


def test_list_checks_ignore_code_and_nested_items():
    checks = list_check_results(
        {"list_checks": {"expected_count": 2, "sequential": True}},
        "1. 第一项\n   1. 子项\n2. 第二项\n```\n3. 代码示例\n```",
    )
    assert all(c["passed"] for c in checks)


def test_confirmed_source_quote_must_exist_and_version_must_be_current(workspace):
    client, _, space, binding, case = prepare(workspace)
    spec = case["payload"]
    spec["criteria"][0]["sources"][0]["quote"] = "不存在的标准答案"
    response = client.put(
        f"/api/spaces/{space}/acceptance/cases/{case['id']}", json={"case": spec, "revision": 1}
    )
    assert response.status_code == 422
    spec["criteria"][0]["sources"][0] = {**binding, "version_id": "not-current"}
    assert (
        client.put(
            f"/api/spaces/{space}/acceptance/cases/{case['id']}", json={"case": spec, "revision": 1}
        ).status_code
        == 404
    )


def test_review_is_independent_and_point_decisions_are_persisted(workspace, monkeypatch):
    client, runtime, space, binding, case = prepare(workspace)
    service = client.app.state.acceptance_service
    captured = []

    def assess(question, answer, points):
        captured.extend(points)
        return [
            {
                "point_id": p["id"],
                "status": "missing",
                "answer_quote": "",
                "source_quote": binding["quote"],
                "note": "答案遗漏了该要求",
                "origin": "model",
            }
            for p in points
        ]

    monkeypatch.setattr(service.assistance, "review_batch", assess)
    run = client.post(
        f"/api/spaces/{space}/acceptance/runs",
        headers={"Idempotency-Key": "v2"},
        json={"name": "V2", "case_ids": [case["id"]], "review_mode": "assisted", "call_limit": 10},
    ).json()
    detail = execute(client, runtime, space, run)
    assert len(captured) == 2
    attempt = detail["attempts"][0]
    assert attempt["state"] == "completed" and attempt["verdict"] == "failed"
    assert attempt["point_summary"]["missing"] == 2
    path = f"/api/spaces/{space}/acceptance/runs/{run['id']}/attempts/{attempt['id']}/reviews"
    rows = [
        {"point_id": f"P{i}", "status": "missing", "answer_quote": "", "note": "复核确认遗漏"}
        for i in range(2)
    ]
    assert (
        client.post(
            path,
            json={
                "verdict": "passed",
                "note": "全部通过",
                "sources_checked": True,
                "point_reviews": rows,
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            path,
            json={
                "verdict": "failed",
                "note": "确认两项遗漏",
                "sources_checked": True,
                "point_reviews": rows,
            },
        ).status_code
        == 200
    )
    saved = client.get(f"/api/spaces/{space}/acceptance/runs/{run['id']}").json()
    assert saved["attempts"][0]["reviews"][-1]["point_reviews"] == rows
    assert saved["attempts"][0]["point_results"][0]["origin"] == "human"


def test_generation_is_budgeted_durable_and_never_auto_confirms(workspace, monkeypatch):
    client, runtime, space, binding, _ = prepare(workspace)
    service = client.app.state.acceptance_service
    called = []

    def model(operation, data):
        from ragkb.application.acceptance_budget import reserve_call

        reserve_call.get()()
        called.append(operation)
        return {
            "cases": [
                {
                    "question": "新候选问题",
                    "criteria": [
                        {
                            "text": "原文规定",
                            "sources": [{"source_id": "S1", "quote": binding["quote"]}],
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(service.assistance.model, "call", model)
    result = client.post(
        f"/api/spaces/{space}/acceptance/candidates:runs",
        headers={"Idempotency-Key": "gen"},
        json={"sources": [binding], "count": 1, "call_limit": 1},
    )
    assert result.status_code == 200, result.text
    assert not called
    run = execute(client, runtime, space, result.json())
    assert run["state"] == "completed" and run["calls_reserved"] == 1
    generated = run["attempts"][0]["payload"]["generated_cases"]
    assert generated[0]["review_state"] == "candidate"
    imported = client.post(
        f"/api/spaces/{space}/acceptance/cases:import", json={"cases": generated}
    )
    assert imported.status_code == 200 and imported.json()["imported"][0]["payload"]["criteria"][0][
        "sources"
    ] == [binding]
    execute(client, runtime, space, result.json())
    assert len(called) == 1


def test_model_cannot_invent_an_answer_witness(workspace, monkeypatch):
    client, _, _, binding, case = prepare(workspace)
    assistance = client.app.state.acceptance_service.assistance
    monkeypatch.setattr(
        assistance.model,
        "call",
        lambda *a: {
            "points": [
                {
                    "point_id": p["id"],
                    "status": "covered",
                    "answer_quote": "不存在的回答",
                    "source_id": "S1",
                    "source_quote": binding["quote"],
                    "note": "已覆盖",
                }
                for p in case["payload"]["criteria"]
            ]
        },
    )
    with pytest.raises(InvalidProviderResponse, match="POINT_REVIEW_WITNESS_INVALID"):
        assistance.review_batch("问题", "只有这一段回答。", case["payload"]["criteria"])


def test_point_regression_cannot_be_hidden_by_equal_overall_status():
    def attempt(status):
        return {"payload": {"point_results": [{"point_id": "P1", "status": status}]}}

    assert compare_points(attempt("covered"), attempt("missing"))["regressed"]


def test_resume_retains_qa_and_completed_review_batches_without_reasking(workspace, monkeypatch):
    from ragkb.application.acceptance_budget import AcceptancePaused

    client, runtime, space, _, case = prepare(workspace, count=7)
    service = client.app.state.acceptance_service
    original = runtime.qa_service.ask
    questions, batches = [], []

    def ask(question, *args, **kwargs):
        questions.append(question)
        assert "核对原文要点" not in question and "criteria" not in kwargs
        return original(question, *args, **kwargs)

    def assess(question, answer, points):
        batches.append([p["id"] for p in points])
        if len(batches) == 2:
            raise AcceptancePaused("CALL_BUDGET_EXHAUSTED")
        return [
            {
                "point_id": p["id"],
                "status": "covered",
                "answer_quote": answer,
                "note": "fixture source review",
                "origin": "model",
            }
            for p in points
        ]

    monkeypatch.setattr(runtime.qa_service, "ask", ask)
    monkeypatch.setattr(service.assistance, "review_batch", assess)
    run = client.post(
        f"/api/spaces/{space}/acceptance/runs",
        headers={"Idempotency-Key": "resume"},
        json={
            "name": "resume",
            "case_ids": [case["id"]],
            "review_mode": "assisted",
            "call_limit": 10,
        },
    ).json()
    first = execute(client, runtime, space, run)
    assert first["state"] == "paused" and first["attempts"][0]["payload"]["qa_complete"]
    final = execute(client, runtime, space, run)
    assert final["state"] == "completed"
    assert len(questions) == 1 and [len(b) for b in batches] == [6, 1, 1]
    assert final["attempts"][-1]["payload"]["reused_qa_receipt"]
    assert final["attempts"][-1]["point_summary"]["covered"] == 7
    assert final["attempts"][-1]["verdict"] == "pending_review"


def test_sources_revoked_hide_point_witnesses_and_reviews(workspace, monkeypatch):
    from ragkb.domain.uploads import ResourceNotFoundError

    client, runtime, space, binding, case = prepare(workspace)
    service = client.app.state.acceptance_service
    run = execute(client, runtime, space, make_run(client, space, [case]))

    def unavailable(*args):
        raise ResourceNotFoundError(binding["chunk_id"])

    monkeypatch.setattr(service.assistance, "bindings", unavailable)
    detail = client.get(f"/api/spaces/{space}/acceptance/runs/{run['id']}").json()
    assert detail["attempts"][0]["payload"]["sources_unavailable"]
    assert not detail["attempts"][0]["point_results"]
    assert not detail["payload"]["cases"][0]["payload"]["criteria"]
    assert binding["quote"] not in str(detail)


def test_generation_rejects_unselected_source_and_does_not_save_candidates(workspace, monkeypatch):
    client, runtime, space, binding, _ = prepare(workspace)
    service = client.app.state.acceptance_service
    monkeypatch.setattr(
        service.assistance.model,
        "call",
        lambda *a: {
            "cases": [
                {
                    "question": "问题",
                    "criteria": [
                        {"text": "伪造依据", "sources": [{"source_id": "S999", "quote": "伪造"}]}
                    ],
                }
            ]
        },
    )
    run = client.post(
        f"/api/spaces/{space}/acceptance/candidates:runs",
        headers={"Idempotency-Key": "bad-source"},
        json={"sources": [binding], "count": 1},
    ).json()
    detail = execute(client, runtime, space, run)
    assert detail["state"] == "paused" and detail["reason"] == "CANDIDATE_SOURCE_WITNESS_INVALID"
    assert not detail["attempts"][0]["payload"].get("generated_cases")


def test_comparison_calls_a_faster_incomplete_answer_quality_regression(workspace, monkeypatch):
    import copy

    client, runtime, space, _, case = prepare(workspace, count=10)
    service = client.app.state.acceptance_service
    run = execute(client, runtime, space, make_run(client, space, [case]))
    before, after = copy.deepcopy(run), copy.deepcopy(run)
    for value, covered, seconds, verdict in ((before, 10, 100, "passed"), (after, 4, 30, "failed")):
        attempt = value["attempts"][0]
        attempt["verdict"] = verdict
        attempt["payload"]["qa_elapsed_seconds"] = seconds
        attempt["payload"]["point_results"] = [
            {"point_id": f"P{i}", "status": "covered" if i < covered else "missing"}
            for i in range(10)
        ]
    monkeypatch.setattr(service, "detail", lambda *args: before if args[2] == "before" else after)
    subject = runtime.authenticator.authenticate(None)
    before["attempts"][0]["payload"]["checks"] = [{"name": "清单条目数量", "passed": True}]
    after["attempts"][0]["payload"]["checks"] = [{"name": "清单条目数量", "passed": False}]
    result = service.compare(subject, space, "before", "after")
    row = result["rows"][0]
    assert row["timing_assessment"] == "faster_with_quality_loss"
    assert row["points"]["before"]["covered"] == 10 and row["points"]["after"]["covered"] == 4
    assert row["points"]["rows"][4]["text"] == "核对原文要点4"
    assert row["checks"] == [{"name": "清单条目数量", "before": True, "after": False}]
    after["payload"]["snapshot"]["evaluation_revision"] = "different-review-policy"
    assert (
        service.compare(subject, space, "before", "after")["rows"][0]["change"] == "not_comparable"
    )


def test_comparison_cost_includes_previous_attempts_used_by_recovery(workspace, monkeypatch):
    import copy

    client, runtime, space, _, case = prepare(workspace)
    service = client.app.state.acceptance_service
    run = execute(client, runtime, space, make_run(client, space, [case]))
    before, after = copy.deepcopy(run), copy.deepcopy(run)
    initial = after["attempts"][0]
    initial["id"] = "qa-receipt"
    resumed = copy.deepcopy(initial)
    initial["state"] = "interrupted"
    resumed["id"] = "continued-review"
    resumed["payload"]["reused_qa_receipt"] = True
    after["attempts"].append(resumed)
    monkeypatch.setattr(service, "detail", lambda *args: before if args[2] == "before" else after)
    calls = [
        {"attempt_id": identity, "payload": {"usage": {"prompt_tokens": tokens}}}
        for identity, tokens in (("qa-receipt", 100), ("continued-review", 20), ("other-case", 900))
    ]
    monkeypatch.setattr(
        service.repository, "calls", lambda identity: calls if identity == "after" else []
    )
    result = service.compare(runtime.authenticator.authenticate(None), space, "before", "after")
    assert result["common_after_usage"]["observed_calls"] == 2
    assert result["common_after_usage"]["input_tokens"] == 120
