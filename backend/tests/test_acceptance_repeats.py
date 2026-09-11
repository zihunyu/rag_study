from copy import deepcopy

from ragkb.application.acceptance_budget import AcceptancePaused
from ragkb.domain.acceptance_repeats import compare_repeats, summarize_repeats
from test_acceptance import execute, make_case
from test_workspace_redesign import workspace as workspace


def sample(run_id="r", times=(10, 20, 90)):
    return {
        "id": run_id,
        "payload": {
            "repeat_count": 3,
            "snapshot": {"code_revision": run_id},
            "cases": [{"id": "c", "payload": {"key": "Q"}}],
        },
        "attempts": [
            {
                "id": f"{run_id}-{n}",
                "case_id": "c",
                "state": "completed",
                "verdict": "pending_review",
                "reviews": [],
                "payload": {
                    "repetition": n,
                    "qa_elapsed_seconds": seconds,
                    "checks": [{"passed": True}],
                    "steps": [{"result": {"status": "answered"}}],
                    "point_results": [{"point_id": "P", "status": "covered", "origin": "model"}],
                },
            }
            for n, seconds in enumerate(times, 1)
        ],
    }


def test_median_worst_and_three_independent_review_counts():
    row = summarize_repeats(sample())["rows"][0]
    assert row["median_seconds"] == 20 and row["worst_seconds"] == 90
    assert row["mechanical_passed"] == row["assisted_passed"] == 3
    assert row["human_passed"] == 0


def test_active_attempt_is_not_displayed_as_system_failure():
    run = sample()
    active = run["attempts"][0]
    active["state"] = "running"
    active["payload"] = {"repetition": 1}
    summary = summarize_repeats(run)
    assert summary["rows"][0]["samples"][0]["group"] == "running"
    assert summary["rows"][0]["completed"] == 2
    assert "system_failure" not in {g["group"] for g in summary["groups"]}


def test_recovery_counts_spent_work_without_double_counting_reused_qa():
    run = sample()
    original = run["attempts"][0]
    original["state"] = "incomplete"
    retry = deepcopy(original)
    retry["id"] = "retry"
    retry["state"] = "completed"
    retry["payload"]["reused_qa_receipt"] = True
    run["attempts"].insert(1, retry)
    row = summarize_repeats(run)["rows"][0]
    assert row["median_seconds"] == 20
    assert row["samples"][0]["seconds"] == 10
    assert row["samples"][0]["retries"] == 1
    # A failed QA attempt that was actually repeated incurs both active durations.
    retry["payload"].pop("reused_qa_receipt")
    assert summarize_repeats(run)["rows"][0]["samples"][0]["seconds"] == 20


def test_fast_missing_answer_and_pending_review_never_become_verified_speedup():
    old, new = sample("old"), sample("new", (2, 3, 4))
    quality = [{"case_id": "c", "change": "pending"}]
    result = compare_repeats(old, new, quality)
    assert result["rows"][0]["assessment"] == "human_review_pending"
    assert result["snapshot_differences"] == ["code_revision"]
    new["attempts"][1]["payload"]["steps"][0]["result"]["status"] = "insufficient_evidence"
    result = compare_repeats(old, new, quality)["rows"][0]
    assert result["assessment"] == "outcome_changed" and result["median_change_percent"] is None


def test_earlier_repetition_failure_is_not_hidden_by_latest_success():
    old, new = sample("old"), sample("new", (2, 3, 4))
    new["attempts"][0]["payload"]["point_results"][0]["status"] = "missing"
    row = compare_repeats(old, new, [{"case_id": "c", "change": "pending"}])["rows"][0]
    assert row["assessment"] == "quality_failed"


def test_repeat_count_and_unknown_durations_are_not_comparable():
    old, new = sample("old"), sample("new")
    quality = [{"case_id": "c", "change": "pending"}]
    new["payload"]["repeat_count"] = 2
    assert compare_repeats(old, new, quality)["rows"][0]["assessment"] == "repeat_count_changed"
    new["payload"]["repeat_count"] = 3
    del new["attempts"][0]["payload"]["qa_elapsed_seconds"]
    assert compare_repeats(old, new, quality)["rows"][0]["assessment"] == "incomplete"


def test_each_repetition_executes_new_qa_and_resume_only_retries_its_own_sample(
    workspace, monkeypatch
):
    client, runtime, _, space = workspace
    case = make_case(client, space)
    run = client.post(
        f"/api/spaces/{space}/acceptance/runs",
        headers={"Idempotency-Key": "repeat"},
        json={"name": "three", "case_ids": [case["id"]], "call_limit": 300, "repeat_count": 3},
    ).json()
    original = runtime.qa_service.ask
    calls = []

    def ask(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise AcceptancePaused("MODEL_PROVIDER_RATE_LIMITED")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.qa_service, "ask", ask)
    first = execute(client, runtime, space, run)
    assert first["state"] == "paused"
    assert [a["payload"]["repetition"] for a in first["attempts"]] == [1, 2]
    final = execute(client, runtime, space, run)
    assert final["state"] == "completed" and len(calls) == 4
    assert [a["payload"]["repetition"] for a in final["attempts"]] == [1, 2, 2, 3]
    assert final["repeat_summary"]["rows"][0]["completed"] == 3
    assert final["repeat_summary"]["rows"][0]["human_passed"] == 0


def test_repeat_budget_includes_all_repetitions(workspace, monkeypatch):
    from ragkb.application.acceptance_budget import reserve_call

    client, runtime, _, space = workspace
    case = make_case(client, space)
    run = client.post(
        f"/api/spaces/{space}/acceptance/runs",
        headers={"Idempotency-Key": "budget-repeat"},
        json={"name": "bounded", "case_ids": [case["id"]], "call_limit": 2, "repeat_count": 3},
    ).json()
    original = runtime.qa_service.ask

    def ask(*args, **kwargs):
        reserve_call.get()()
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.qa_service, "ask", ask)
    result = execute(client, runtime, space, run)
    assert result["state"] == "paused" and result["reason"] == "CALL_BUDGET_EXHAUSTED"
    assert result["calls_reserved"] == 2
    assert result["repeat_summary"]["rows"][0]["completed"] == 2
