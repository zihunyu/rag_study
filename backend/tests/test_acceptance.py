from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from ragkb.adapters.model_http import HttpxJsonTransport
from ragkb.application.acceptance_budget import AcceptancePaused, acceptance_budget
from ragkb.domain.acceptance import AcceptanceCase
from ragkb.domain.uploads import ResourceNotFoundError
from test_password_accounts import accounts as accounts
from test_password_accounts import add_user, assign
from test_workspace_redesign import publish, upload
from test_workspace_redesign import workspace as workspace


def candidate(key="Q01", confirmed=True):
    return AcceptanceCase(
        key=key,
        question="资料中没有的保修期是多少？",
        expected_status="insufficient_evidence",
        source_notes="空库没有可用资料，应拒答。",
        review_state="confirmed" if confirmed else "candidate",
    ).model_dump(mode="json")


def make_case(client, space, key="Q01", confirmed=True):
    response = client.post(
        f"/api/spaces/{space}/acceptance/cases", json={"case": candidate(key, confirmed)}
    )
    assert response.status_code == 200, response.text
    return response.json()


def make_run(client, space, cases, key="run"):
    response = client.post(
        f"/api/spaces/{space}/acceptance/runs",
        json={"name": key, "case_ids": [c["id"] for c in cases], "call_limit": 4},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 200, response.text
    return response.json()


def execute(client, runtime, space, run):
    service = client.app.state.acceptance_service
    subject = runtime.authenticator.authenticate(None)
    service._execute(subject, space, run["id"])
    response = client.get(f"/api/spaces/{space}/acceptance/runs/{run['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def test_case_revisions_and_frozen_run_do_not_follow_edits(workspace):
    client, _, _, space = workspace
    case = make_case(client, space)
    run = make_run(client, space, [case])
    edited = {**case["payload"], "question": "资料中没有的电压是多少？"}
    path = f"/api/spaces/{space}/acceptance/cases/{case['id']}"
    assert client.put(path, json={"case": edited, "revision": 1}).status_code == 200
    assert client.put(path, json={"case": edited, "revision": 1}).status_code == 412
    versions = client.get(path + "/revisions").json()
    assert [v["revision"] for v in versions] == [2, 1]
    saved = client.get(f"/api/spaces/{space}/acceptance/runs/{run['id']}").json()
    assert saved["payload"]["cases"][0]["payload"]["question"] == case["payload"]["question"]


def test_candidates_cannot_run_and_import_cannot_claim_approval(workspace):
    client, _, _, space = workspace
    case = make_case(client, space, confirmed=False)
    response = client.post(
        f"/api/spaces/{space}/acceptance/runs",
        headers={"Idempotency-Key": "r"},
        json={"name": "r", "case_ids": [case["id"]], "call_limit": 2},
    )
    assert response.status_code == 422
    path = f"/api/spaces/{space}/acceptance/cases:import"
    imported = client.post(path, json={"cases": [candidate("Q02")]}).json()["imported"]
    assert imported[0]["payload"]["review_state"] == "candidate"
    duplicate = client.post(path, json={"cases": [candidate("Q02")]}).json()
    assert not duplicate["imported"] and duplicate["skipped_existing"] == ["Q02"]


def test_actual_local_qa_refusal_requires_independent_review(workspace):
    client, runtime, _, space = workspace
    case = make_case(client, space)
    run = execute(client, runtime, space, make_run(client, space, [case]))
    assert run["state"] == "completed", run
    attempt = run["attempts"][0]
    assert attempt["verdict"] == "pending_review"
    assert all(c["passed"] for c in attempt["payload"]["checks"])
    path = f"/api/spaces/{space}/acceptance/runs/{run['id']}/attempts/{attempt['id']}/reviews"
    assert client.post(path, json={"verdict": "passed", "note": "正确"}).status_code == 422
    assert (
        client.post(
            path,
            json={"verdict": "passed", "note": "空库无资料，正确拒答。", "sources_checked": True},
        ).status_code
        == 200
    )
    revised = client.get(f"/api/spaces/{space}/acceptance/runs/{run['id']}").json()
    assert revised["attempts"][0]["verdict"] == "passed"
    assert revised["attempts"][0]["payload"] == attempt["payload"]


def test_outer_deadline_keeps_qa_receipt_and_reports_stable_reason(workspace, monkeypatch):
    from contextlib import contextmanager

    from ragkb.domain.errors import ProviderTimeout

    client, runtime, _, space = workspace
    spec = candidate()
    spec["reading"]["mode"] = "fact"
    case = client.post(f"/api/spaces/{space}/acceptance/cases", json={"case": spec}).json()
    run = make_run(client, space, [case])

    @contextmanager
    def expire_after_qa(seconds):
        assert seconds == runtime.settings.qa_fact_timeout_seconds
        yield
        raise ProviderTimeout("REQUEST_DEADLINE_EXCEEDED")

    monkeypatch.setattr("ragkb.infrastructure.acceptance_service.request_deadline", expire_after_qa)
    detail = execute(client, runtime, space, run)
    assert detail["state"] == "paused"
    assert detail["reason"] == "REQUEST_DEADLINE_EXCEEDED"
    attempt = detail["attempts"][0]
    assert attempt["state"] == "incomplete"
    assert attempt["payload"]["elapsed_seconds"] >= 0
    result = attempt["payload"]["steps"][0]["result"]
    assert result["status"] == "insufficient_evidence"
    assert runtime.rag_repository.get_package(result["rag_run_id"]) is not None


@pytest.mark.parametrize("endpoint", ["/api/ask", "/api/ask:stream"])
@pytest.mark.parametrize("mode, expected", [("fact", 47), ("auto", 222)])
def test_qa_http_deadlines_follow_the_configured_reading_budget(
    workspace, monkeypatch, endpoint, mode, expected
):
    from contextlib import contextmanager

    client, runtime, _, space = workspace
    monkeypatch.setitem(runtime.settings.__dict__, "qa_fact_timeout_seconds", 47)
    monkeypatch.setitem(runtime.settings.__dict__, "overview_timeout_seconds", 222)
    observed = []

    @contextmanager
    def capture(seconds):
        observed.append(seconds)
        yield

    monkeypatch.setattr("ragkb.api.routers.rag.request_deadline", capture)
    response = client.post(
        endpoint,
        json={
            "question": "资料中没有的保修期是多少？",
            "space_id": space,
            "reading": {"mode": mode},
        },
    )
    assert response.status_code == 200
    assert observed == [expected]


def test_lease_recovery_fences_old_worker_and_retains_attempt(workspace):
    client, runtime, _, space = workspace
    run = make_run(client, space, [make_case(client, space)])
    repo = client.app.state.acceptance_service.repository
    assert repo.claim(space, run["id"], "old")
    attempt = repo.begin_attempt(run["id"], "old", run["payload"]["cases"][0]["id"])
    repo.reserve(run["id"], "old")
    with repo.db.transaction() as c:
        repo.db.execute(c, "UPDATE acceptance_runs SET lease_until=? WHERE id=?", (0, run["id"]))
    repo.recover()
    assert repo.run(space, run["id"])["reason"] == "WORKER_INTERRUPTED"
    repo.finish_attempt(run["id"], "old", attempt, "completed", {"bad": True})
    resumed = execute(client, runtime, space, run)
    assert [a["state"] for a in resumed["attempts"]] == ["incomplete", "completed"]
    assert resumed["calls_reserved"] == 1
    assert "bad" not in resumed["attempts"][0]["payload"]


def test_atomic_claim_and_budget_are_durable(workspace):
    client, _, _, space = workspace
    run = make_run(client, space, [make_case(client, space)])
    repo = client.app.state.acceptance_service.repository
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda n: repo.claim(space, run["id"], str(n)), range(4)))
    assert sum(claims) == 1
    token = str(claims.index(True))
    for _ in range(4):
        repo.reserve(run["id"], token)
    with pytest.raises(AcceptancePaused, match="CALL_BUDGET_EXHAUSTED"):
        repo.reserve(run["id"], token)
    repo.finish_run(run["id"], token, "paused", "CALL_BUDGET_EXHAUSTED")
    repo.increase_budget(space, run["id"], 6)
    repo.claim(space, run["id"], "new")
    repo.reserve(run["id"], "new")
    assert repo.run(space, run["id"])["calls_reserved"] == 5


def test_http_retries_consume_real_call_budget_without_leaking_to_other_requests(workspace):
    client, runtime, _, space = workspace
    run = make_run(client, space, [make_case(client, space)])
    repo = client.app.state.acceptance_service.repository
    repo.claim(space, run["id"], "budget")
    settings = runtime.settings.model_copy(
        update={
            "model_account_limit_enabled": False,
            "model_usage_enabled": False,
            "model_http_max_retries": 5,
            "model_http_circuit_failure_threshold": 50,
        }
    )
    transport = HttpxJsonTransport(settings)
    transport._client.close()
    sent, observed = [], []

    def reply(request):
        sent.append(request)
        return httpx.Response(503)

    transport._client = httpx.Client(transport=httpx.MockTransport(reply))
    transport._delay = lambda *a, **kw: None
    try:
        with acceptance_budget(lambda: repo.reserve(run["id"], "budget"), observed.append):
            with pytest.raises(AcceptancePaused, match="CALL_BUDGET_EXHAUSTED"):
                transport.post_json(
                    "https://fixture.invalid/chat", headers={}, payload={}, timeout=5
                )
        assert len(sent) == len(observed) == 4
        transport._client.close()
        transport._client = httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
        )
        assert transport.post_json(
            "https://fixture.invalid/chat", headers={}, payload={}, timeout=5
        )["ok"]
        assert repo.run(space, run["id"])["calls_reserved"] == 4
    finally:
        transport.close()


def test_resume_skips_completed_cases_and_keeps_partial_failure(workspace, monkeypatch):
    client, runtime, _, space = workspace
    cases = [make_case(client, space, "Q01"), make_case(client, space, "Q02")]
    run = make_run(client, space, cases)
    actual, seen = runtime.qa_service.ask, []

    def ask(*args, **kwargs):
        seen.append(args[0])
        if len(seen) == 2:
            raise AcceptancePaused("MODEL_SERVICE_UNAVAILABLE")
        return actual(*args, **kwargs)

    monkeypatch.setattr(runtime.qa_service, "ask", ask)
    first = execute(client, runtime, space, run)
    assert first["state"] == "paused"
    assert [a["state"] for a in first["attempts"]] == ["completed", "incomplete"]
    second = execute(client, runtime, space, run)
    assert second["state"] == "completed" and len(seen) == 3
    assert [a["attempt"] for a in second["attempts"]] == [1, 1, 2]


def test_snapshot_change_blocks_resume_and_comparison_does_not_hide_case_changes(workspace):
    client, runtime, _, space = workspace
    case = make_case(client, space)
    first = make_run(client, space, [case], "before")
    service = client.app.state.acceptance_service
    service.code_revision = "new-deployment"
    response = client.post(f"/api/spaces/{space}/acceptance/runs/{first['id']}:resume")
    assert response.status_code == 409
    updated = {**case["payload"], "source_notes": "另一个人工确认依据"}
    client.put(
        f"/api/spaces/{space}/acceptance/cases/{case['id']}", json={"case": updated, "revision": 1}
    )
    second = make_run(client, space, [case], "after")
    response = client.get(
        f"/api/spaces/{space}/acceptance/compare?baseline={first['id']}&candidate={second['id']}"
    )
    assert response.json()["rows"][0]["change"] == "not_comparable"


def test_run_idempotency_and_cross_space_identity(workspace):
    client, runtime, _, space = workspace
    case = make_case(client, space)
    run = make_run(client, space, [case])
    assert make_run(client, space, [case])["id"] == run["id"]
    other = client.post("/api/spaces", json={"name": "other"}).json()["id"]
    assert client.get(f"/api/spaces/{other}/acceptance/runs/{run['id']}").status_code == 404
    with pytest.raises(ResourceNotFoundError):
        client.app.state.acceptance_service.repository.run(other, run["id"])


def test_manager_permissions_and_admin_only_private_diagnostics(accounts):
    runtime, app, admin = accounts
    space = runtime.space_id
    other = admin.post("/api/spaces", json={"name": "另一个库"}).json()["id"]
    uid, manager = add_user(app, admin, "acceptance_manager")
    assign(admin, space, uid, "manager")
    assign(admin, other, uid, "qa")
    assert manager.get(f"/api/spaces/{space}/acceptance/cases").status_code == 200
    assert manager.get(f"/api/spaces/{other}/acceptance/cases").status_code == 404
    case = make_case(manager, space)
    run = make_run(manager, space, [case])
    repo = app.state.acceptance_service.repository
    repo.claim(space, run["id"], "t")
    attempt = repo.begin_attempt(run["id"], "t", case["id"])
    repo.finish_attempt(
        run["id"],
        "t",
        attempt,
        "completed",
        {
            "steps": [
                {
                    "result": {"status": "insufficient_evidence", "answer": None, "citations": []},
                    "evidence": [],
                    "diagnostics": {
                        "calls": [{"draft": "PRIVATE-DRAFT-FIXTURE"}],
                        "failure": {
                            "stage": "verification",
                            "code": "FAIL",
                            "draft": "PRIVATE-DRAFT-FIXTURE",
                            "detail": {
                                "condition_id": "K5",
                                "evidence_id": "E5",
                                "reason": "condition_source_not_cited",
                                "draft": "PRIVATE-DRAFT-FIXTURE",
                            },
                        },
                    },
                }
            ],
            "checks": [],
        },
    )
    repo.finish_run(run["id"], "t", "completed")
    path = f"/api/spaces/{space}/acceptance/runs/{run['id']}"
    assert "PRIVATE-DRAFT-FIXTURE" not in manager.get(path).text
    public = manager.get(path).json()
    assert "execution_token" not in public
    assert public["attempts"][0]["payload"]["steps"][0]["failure"] == {
        "stage": "verification",
        "code": "FAIL",
        "condition_id": "K5",
        "evidence_id": "E5",
        "reason": "condition_source_not_cited",
        "condition_ids": [],
        "conflicting_evidence_ids": [],
    }
    assert manager.get(path + f"/attempts/{attempt}/diagnostics").status_code == 403
    assert "PRIVATE-DRAFT-FIXTURE" in admin.get(path + f"/attempts/{attempt}/diagnostics").text
    assign(admin, space, uid, "qa")
    assert manager.get(path).status_code in {401, 403, 404}


def test_fresh_answers_and_current_document_permission_recheck(workspace, monkeypatch):
    client, runtime, _, space = workspace
    document = upload(client, runtime, space)
    publish(client, document["document_version_id"])
    spec = AcceptanceCase(
        key="fresh",
        question="星云 X1 的保修期是多久？",
        required_points=["三年"],
        source_notes="服务政策原文",
        review_state="confirmed",
        required_source_documents=[document["document_id"]],
    )
    saved = client.post(
        f"/api/spaces/{space}/acceptance/cases", json={"case": spec.model_dump(mode="json")}
    ).json()

    class NoCache:
        def get(self, package):
            raise AssertionError("acceptance cannot reuse an answer")

        def put(self, package, draft):
            raise AssertionError("acceptance cannot populate the user answer cache")

    monkeypatch.setattr(runtime.qa_service, "cache", NoCache())
    run = execute(client, runtime, space, make_run(client, space, [saved]))
    assert run["state"] == "completed", run
    assert run["attempts"][0]["payload"]["steps"][0]["evidence"]
    monkeypatch.setattr(runtime.qa_service.permission, "recheck", lambda *a, **k: False)
    detail = client.get(f"/api/spaces/{space}/acceptance/runs/{run['id']}").json()
    step = detail["attempts"][0]["payload"]["steps"][0]
    assert step["result"]["status"] == "sources_unavailable" and not step["evidence"]
    attempt_id = detail["attempts"][0]["id"]
    path = f"/api/spaces/{space}/acceptance/runs/{run['id']}/attempts/{attempt_id}/diagnostics"
    assert client.get(path).status_code == 404


def test_session_rotation_does_not_change_run_configuration(workspace):
    from dataclasses import replace

    client, runtime, _, space = workspace
    case = make_case(client, space)
    service = client.app.state.acceptance_service
    subject = runtime.authenticator.authenticate(None)
    first = replace(subject, scope_tokens=(*subject.scope_tokens, "auth-session:first"))
    second = replace(subject, scope_tokens=(*subject.scope_tokens, "auth-session:second"))
    assert service.snapshot(first, space, [case]) == service.snapshot(second, space, [case])


def test_subset_retest_ignores_other_cases_sources_and_compares_common_usage(workspace):
    client, runtime, _, space = workspace
    foreign_space = client.post("/api/spaces", json={"name": "隔离干扰资料"}).json()["id"]
    foreign = upload(client, runtime, foreign_space)
    first = make_case(client, space)
    spec = candidate("isolation")
    spec["reading"]["document_ids"] = [foreign["document_id"]]
    second = client.post(f"/api/spaces/{space}/acceptance/cases", json={"case": spec}).json()
    baseline = make_run(client, space, [first, second], "baseline")
    retest = make_run(client, space, [first], "retest")
    repo = client.app.state.acceptance_service.repository
    for run, cases in ((baseline, [first, second]), (retest, [first])):
        assert repo.claim(space, run["id"], "worker")
        for case in cases:
            identity = repo.begin_attempt(run["id"], "worker", case["id"])
            count = 2 if run == baseline and case == first else 1
            for _ in range(count):
                repo.reserve(run["id"], "worker")
                repo.observe(run["id"], identity, {"usage": {"prompt_tokens": 10}, "cost_cny": 0.1})
            state = "failed" if run == baseline and case == first else "completed"
            repo.finish_attempt(run["id"], "worker", identity, state, {"steps": [], "checks": []})
            if run == retest:
                repo.review(run["id"], identity, "reviewer", "passed", "已对照原文")
        repo.finish_run(run["id"], "worker", "completed")
    report = client.get(
        f"/api/spaces/{space}/acceptance/compare?baseline={baseline['id']}&candidate={retest['id']}"
    ).json()
    assert report["rows"][0]["change"] == "improved"
    assert report["rows"][1]["change"] == "removed"
    assert report["common_case_count"] == 1
    assert report["before_usage"]["observed_calls"] == 3
    assert report["common_before_usage"]["observed_calls"] == 2
    assert report["common_after_usage"]["observed_calls"] == 1
    assert report["common_before_usage"]["known_cost_cny"] == 0.2
