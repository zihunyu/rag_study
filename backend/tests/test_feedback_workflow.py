import sqlite3
from dataclasses import replace

import pytest
from ragkb.infrastructure.feedback_workflow import FeedbackWorkflow
from test_acceptance import execute, make_run
from test_password_accounts import accounts as accounts
from test_password_accounts import add_user, assign
from test_workspace_redesign import workspace as workspace


def report(workspace, rating=1):
    client, runtime, _, space = workspace
    answer = client.post(
        "/api/ask", json={"space_id": space, "question": "资料中没有的保修期是多少？"}
    )
    assert answer.status_code == 200, answer.text
    run = answer.json()["rag_run_id"]
    body = {"rating": rating, "reason_code": "missing_answer", "comment": "需要检查是否漏读资料"}
    saved = client.post(f"/api/rag-runs/{run}/feedback", json=body)
    assert saved.status_code == 200, saved.text
    return saved.json(), run, body


def action(client, space, item, action, **kwargs):
    return client.post(
        f"/api/spaces/{space}/feedback-work-items/{item['id']}/actions",
        json={"revision": item["revision"], "action": action, "note": "本轮处理说明", **kwargs},
    )


def prepare(workspace):
    client, _, _, space = workspace
    receipt, _, _ = report(workspace)
    path = f"/api/spaces/{space}/feedback-work-items"
    item = client.get(path + "/" + receipt["work_item_id"]).json()
    created = action(client, space, item, "create_case")
    assert created.status_code == 200, created.text
    item = created.json()
    case = next(
        c
        for c in client.get(f"/api/spaces/{space}/acceptance/cases").json()
        if c["id"] == item["payload"]["case_id"]
    )
    assert case["payload"]["review_state"] == "candidate"
    assert not case["payload"]["required_points"] and not case["payload"]["criteria"]
    confirmed = {
        **case["payload"],
        "expected_status": "insufficient_evidence",
        "review_state": "confirmed",
        "source_notes": "人工核查本测试知识库为空，应拒答。",
    }
    response = client.put(
        f"/api/spaces/{space}/acceptance/cases/{case['id']}",
        json={"revision": case["revision"], "case": confirmed},
    )
    assert response.status_code == 200, response.text
    case = response.json()
    ready = action(client, space, item, "ready_for_retest")
    assert ready.status_code == 200, ready.text
    return ready.json(), case


def test_low_rating_saved_atomically_idempotently_and_scoped(workspace):
    client, runtime, _, space = workspace
    receipt, run, body = report(workspace)
    replay = client.post(f"/api/rag-runs/{run}/feedback", json=body)
    assert replay.json() == receipt
    items = client.get(f"/api/spaces/{space}/feedback-work-items").json()
    assert len(items) == 1 and items[0]["state"] == "open"
    assert items[0]["payload"]["feedback"]["rag_run_id"] == run
    assert items[0]["payload"]["feedback"]["space_id"] == space
    assert (
        client.get(
            f"/api/spaces/{runtime.space_id}/feedback-work-items/{items[0]['id']}"
        ).status_code
        == 404
    )
    claimed = action(client, space, items[0], "claim")
    assert claimed.status_code == 200 and claimed.json()["payload"]["owner_id"]
    assert action(client, space, items[0], "claim").status_code == 412
    report(workspace, rating=5)
    assert len(client.get(f"/api/spaces/{space}/feedback-work-items").json()) == 1


def test_feedback_failure_rolls_back_original_feedback(workspace, monkeypatch):
    _, runtime, _, space = workspace
    report(workspace)
    service = FeedbackWorkflow(workspace[0].app.state.acceptance_service)
    original = service.list(space)[0]["payload"]["feedback"]
    from ragkb.domain.rag import Feedback

    feedback = replace(Feedback(**original), feedback_id="failure-rollback")
    from ragkb.infrastructure import feedback_capture

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("injected")

    monkeypatch.setattr(feedback_capture, "capture", fail)
    with pytest.raises(sqlite3.OperationalError):
        runtime.rag_repository.save_feedback(feedback)
    with service.db.connection() as c:
        assert (
            service.db.one(c, "SELECT id FROM user_feedback WHERE id=?", (feedback.feedback_id,))
            is None
        )


def test_feedback_requires_new_reviewed_retest_and_reopen_invalidates_old_result(workspace):
    client, runtime, _, space = workspace
    item, case = prepare(workspace)
    run = execute(client, runtime, space, make_run(client, space, [case], "feedback-retest"))
    attempt = run["attempts"][0]
    assert action(client, space, item, "record_retest", attempt_id=attempt["id"]).status_code == 422
    repo = client.app.state.acceptance_service.repository
    review = repo.review(run["id"], attempt["id"], "reviewer", "passed", "已对照原文验收")
    finished = action(client, space, item, "record_retest", attempt_id=attempt["id"])
    assert finished.status_code == 200, finished.text
    closed = finished.json()
    assert closed["state"] == "resolved"
    assert closed["payload"]["resolution"]["review_id"] == review["id"]
    opened = action(client, space, closed, "reopen").json()
    ready = action(client, space, opened, "ready_for_retest").json()
    stale = action(client, space, ready, "record_retest", attempt_id=attempt["id"])
    assert stale.status_code == 422 and "FRESH" in stale.text


def test_failed_human_retest_returns_to_work_and_changed_case_cannot_close(workspace):
    client, runtime, _, space = workspace
    item, case = prepare(workspace)
    run = execute(client, runtime, space, make_run(client, space, [case], "failed-retest"))
    attempt = run["attempts"][0]
    repo = client.app.state.acceptance_service.repository
    repo.review(run["id"], attempt["id"], "reviewer", "failed", "仍有缺口")
    result = action(client, space, item, "record_retest", attempt_id=attempt["id"])
    assert result.status_code == 200, result.text
    assert result.json()["state"] == "in_progress"
    assert result.json()["payload"]["resolution"]["verdict"] == "failed"


def test_case_creation_and_link_roll_back_together(workspace, monkeypatch):
    client, _, _, space = workspace
    report(workspace)
    service = FeedbackWorkflow(client.app.state.acceptance_service)
    item = service.list(space)[0]
    original = service.db.execute

    def fail(c, sql, values=()):
        if sql.startswith("UPDATE feedback_work_items"):
            raise sqlite3.OperationalError("injected final update failure")
        return original(c, sql, values)

    monkeypatch.setattr(service.db, "execute", fail)
    with pytest.raises(sqlite3.OperationalError):
        service.update(space, item["id"], "manager", 1, "create_case", "创建回归案例")
    assert service.repo.cases(space) == []
    assert service.detail(space, item["id"])["revision"] == 1


def test_managers_are_scoped_and_readers_cannot_see_feedback_or_removal_work(accounts):
    runtime, app, admin = accounts
    other = admin.post("/api/spaces", json={"name": "另一个资料库"}).json()["id"]
    uid, user = add_user(app, admin, "feedback-manager")
    assign(admin, runtime.space_id, uid, "manager")
    assign(admin, other, uid, "qa")
    for suffix in ("feedback-work-items", "directory-removals"):
        assert user.get(f"/api/spaces/{runtime.space_id}/{suffix}").status_code == 200
        assert user.get(f"/api/spaces/{other}/{suffix}").status_code == 404


def test_mysql_feedback_capture_transaction_and_replay(tmp_path):
    from mysql_sql_harness import SQLControl
    from ragkb.adapters.mysql_rag import MySQLRAGRunRepository
    from ragkb.domain.rag import Feedback
    from ragkb.infrastructure.workspace_schema import sqlite_workspace_schema

    path = tmp_path / "mysql-contract.sqlite3"
    control = SQLControl(path)
    with sqlite3.connect(path) as connection:
        connection.executescript(sqlite_workspace_schema())
    repo = MySQLRAGRunRepository(control)
    feedback = Feedback(
        "run",
        "reader",
        1,
        "wrong",
        "有遗漏",
        "index",
        "retrieval",
        "prompt",
        "model",
        feedback_id="f1",
        tenant_id="tenant",
        space_id="space",
        question="问题",
    )
    repo.save_feedback(feedback)
    repo.save_feedback(feedback)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM rag_feedback").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM feedback_work_items").fetchone()[0] == 1
    control.fail_match = "INSERT INTO feedback_work_items"
    with pytest.raises(ConnectionError):
        repo.save_feedback(replace(feedback, feedback_id="f2"))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM rag_feedback").fetchone()[0] == 1


def test_editing_case_after_retest_blocks_resolution(workspace):
    client, runtime, _, space = workspace
    item, case = prepare(workspace)
    run = execute(client, runtime, space, make_run(client, space, [case], "stale-case"))
    attempt = run["attempts"][0]
    repo = client.app.state.acceptance_service.repository
    repo.review(run["id"], attempt["id"], "reviewer", "passed", "旧版本验收")
    repo.save_case(
        space,
        "manager",
        {**case["payload"], "source_notes": "资料解释已修改"},
        case["revision"],
        case["id"],
    )
    result = action(client, space, item, "record_retest", attempt_id=attempt["id"])
    assert result.status_code == 422, result.text
    assert (
        client.get(f"/api/spaces/{space}/feedback-work-items/{item['id']}").json()["state"]
        == "ready_for_retest"
    )


def test_changing_linked_case_to_an_unrelated_question_cannot_resolve_feedback(workspace):
    client, _, _, space = workspace
    item, case = prepare(workspace)
    repo = client.app.state.acceptance_service.repository
    repo.save_case(
        space,
        "manager",
        {**case["payload"], "question": "另一个产品的续航多久？"},
        case["revision"],
        case["id"],
    )
    response = action(client, space, item, "ready_for_retest")
    assert response.status_code == 422 and "QUESTION_MISMATCH" in response.text


def test_failed_acceptance_attempt_can_be_recorded_but_cannot_close_feedback(workspace):
    client, runtime, _, space = workspace
    item, case = prepare(workspace)
    run = execute(client, runtime, space, make_run(client, space, [case], "technical-failure"))
    attempt = run["attempts"][0]
    repo = client.app.state.acceptance_service.repository
    with repo.db.transaction() as c:
        repo.db.execute(
            c, "UPDATE acceptance_attempts SET state='failed' WHERE id=?", (attempt["id"],)
        )
    repo.review(run["id"], attempt["id"], "reviewer", "failed", "验收未通过，需要继续修复")
    response = action(client, space, item, "record_retest", attempt_id=attempt["id"])
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "in_progress"
    assert response.json()["payload"]["resolution"]["attempt_id"] == attempt["id"]
