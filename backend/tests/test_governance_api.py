from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.domain.governance import FINAL_REAL_EVIDENCE_REQUIREMENTS
from ragkb.runtime_components import build_runtime_components


def _client(tmp_path: Path) -> TestClient:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    return TestClient(create_app(components))


def _headers(key: str, revision: int | None = None) -> dict[str, str]:
    result = {"Idempotency-Key": key}
    if revision is not None:
        result["If-Match"] = f'"{revision}"'
    return result


def _evidence(client: TestClient, revision: str) -> dict[str, object]:
    return client.post(
        "/api/v1/admin/evidence-index",
        json={"category": "uat", "revision": revision, "metadata": {"synthetic": True}},
    ).json()


def test_observability_and_immutable_evidence_index(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/v1/admin/diagnostics").status_code == 200
    body = {"category": "security", "revision": "rev-1", "metadata": {"passed": 8}}
    first = client.post("/api/v1/admin/evidence-index", json=body)
    replay = client.post("/api/v1/admin/evidence-index", json=body)
    conflict = client.post("/api/v1/admin/evidence-index", json={**body, "metadata": {"passed": 7}})
    risk_body = {
        "category": "RISK",
        "title": "Synthetic capacity risk",
        "owner": "sre",
        "state": "OPEN",
        "revision": "risk-rev-1",
        "metadata": {"simulated": True},
    }
    risk = client.post("/api/v1/admin/governance-register", json=risk_body)
    duplicate_risk = client.post(
        "/api/v1/admin/governance-register", json={**risk_body, "title": "changed"}
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["content_hash"] == replay.json()["content_hash"]
    assert conflict.status_code == 409
    assert risk.status_code == 200 and duplicate_risk.status_code == 409
    diagnostics = client.get("/api/v1/admin/diagnostics").json()
    assert diagnostics["event_count"] > 0
    assert diagnostics["otel_export_performed"] is False
    assert diagnostics["real_acceptance"] is False


def test_governance_idempotency_is_stable_conflicting_and_restart_safe(tmp_path: Path) -> None:
    body = {"name": "Restart-safe pilot", "feature_flag": "pilot.restart"}
    first_client = _client(tmp_path)
    first = first_client.post(
        "/api/v1/governance/pilots", headers=_headers("restart-key"), json=body
    )
    restarted = _client(tmp_path)
    replay = restarted.post("/api/v1/governance/pilots", headers=_headers("restart-key"), json=body)
    conflict = restarted.post(
        "/api/v1/governance/pilots",
        headers=_headers("restart-key"),
        json={**body, "name": "different"},
    )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert conflict.status_code == 409
    assert conflict.headers["content-type"].startswith("application/json")


def test_pilot_requires_canary_uat_signoffs_and_rollout_is_idempotent(tmp_path: Path) -> None:
    client = _client(tmp_path)
    create_body = {"name": "Synthetic pilot", "feature_flag": "pilot.synthetic"}
    pilot = client.post(
        "/api/v1/governance/pilots", headers=_headers("pilot-create"), json=create_body
    ).json()
    pilot_id = pilot["pilot_id"]
    blocked = client.post(
        f"/api/v1/governance/pilots/{pilot_id}:evaluate",
        headers=_headers("pilot-eval-blocked", 1),
    ).json()
    assert {"CANARY_REQUIRED", "UAT_SUITE_NOT_PASSED_WITH_EVIDENCE"}.issubset(blocked["blockers"])
    evidence = _evidence(client, "pilot-uat-rev")
    uat = client.post(
        "/api/v1/governance/uat-cases",
        headers=_headers("pilot-uat-create"),
        json={
            "pilot_id": pilot_id,
            "title": "Pilot UAT",
            "steps": ["ask"],
            "expected": ["safe"],
        },
    ).json()
    uat_result = client.put(
        f"/api/v1/governance/uat-cases/{uat['case_id']}/result",
        headers=_headers("pilot-uat-result", 1),
        json={
            "result": "PASSED",
            "step_results": ["safe"],
            "evidence": [
                {
                    "category": "uat",
                    "revision": "pilot-uat-rev",
                    "content_hash": evidence["content_hash"],
                }
            ],
        },
    )
    assert uat_result.status_code == 200
    for role in ("technical", "security", "sre"):
        assert (
            client.post(
                f"/api/v1/governance/pilots/{pilot_id}/signoffs",
                headers=_headers(f"pilot-signoff-{role}", 2),
                json={"role": role, "decision": "APPROVE", "comment": "synthetic"},
            ).status_code
            == 200
        )
    canary = client.post(
        f"/api/v1/governance/pilots/{pilot_id}:canary?seed=20260901",
        headers=_headers("pilot-canary", 2),
    ).json()
    assert canary["result"] == "PASS" and canary["pilot_revision"] == 3
    ready = client.post(
        f"/api/v1/governance/pilots/{pilot_id}:evaluate",
        headers=_headers("pilot-eval-ready", 3),
    ).json()
    assert ready["state"] == "SIMULATED_GO"
    rollout_headers = _headers("pilot-rollout", 4)
    rollout = client.post(f"/api/v1/governance/pilots/{pilot_id}:rollout", headers=rollout_headers)
    replay = client.post(f"/api/v1/governance/pilots/{pilot_id}:rollout", headers=rollout_headers)
    duplicate = client.post(
        f"/api/v1/governance/pilots/{pilot_id}:rollout",
        headers=_headers("pilot-rollout-new-key", 5),
    )
    rolled_back = client.post(
        f"/api/v1/governance/pilots/{pilot_id}:rollback",
        headers=_headers("pilot-rollback", 5),
        json={"trigger": "synthetic threshold"},
    )

    assert rollout.status_code == replay.status_code == 200
    assert rollout.json() == replay.json()
    assert [item["percentage"] for item in rollout.json()] == [5, 25, 50, 100]
    assert duplicate.status_code == 409 and duplicate.headers["content-type"].startswith(
        "application/json"
    )
    assert rolled_back.json()["state"] == "ROLLED_BACK"
    assert rolled_back.json()["real_acceptance"] is False

    failing = client.post(
        "/api/v1/governance/pilots",
        headers=_headers("failing-pilot"),
        json={"name": "Failing canary", "feature_flag": "pilot.fail"},
    ).json()
    failed_canary = client.post(
        f"/api/v1/governance/pilots/{failing['pilot_id']}:canary"
        "?seed=20260901&request_count=20&threshold=0",
        headers=_headers("failing-canary", 1),
    ).json()
    no_go = client.post(
        f"/api/v1/governance/pilots/{failing['pilot_id']}:evaluate",
        headers=_headers("failing-evaluate", 2),
    ).json()
    assert failed_canary["result"] == "FAIL"
    assert "CANARY_FAILED" in no_go["blockers"]


def test_uat_evidence_and_observation_api_fail_closed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    pilot = client.post(
        "/api/v1/governance/pilots",
        headers=_headers("observation-pilot"),
        json={"name": "Observation pilot", "feature_flag": "observation.synthetic"},
    ).json()
    uat = client.post(
        "/api/v1/governance/uat-cases",
        headers=_headers("empty-evidence-uat"),
        json={
            "pilot_id": pilot["pilot_id"],
            "title": "Synthetic UAT",
            "steps": ["ask"],
            "expected": ["safe"],
        },
    ).json()
    empty_evidence = client.put(
        f"/api/v1/governance/uat-cases/{uat['case_id']}/result",
        headers=_headers("empty-evidence-result", 1),
        json={"result": "PASSED", "step_results": ["safe"], "evidence": []},
    )
    assert empty_evidence.status_code == 409

    observation = client.post(
        "/api/v1/governance/observations",
        headers=_headers("observation-create"),
        json={"name": "Synthetic observation"},
    ).json()
    window_id = observation["window_id"]
    metrics = {
        "availability": 1.0,
        "error_rate": 0.0,
        "latency_p95": 0.01,
        "sample_count": 100,
        "coverage_ratio": 1.0,
        "sampling_gap_count": 0,
    }
    updated = client.put(
        f"/api/v1/governance/observations/{window_id}/metrics",
        headers=_headers("observation-metrics", 1),
        json={"metrics": metrics},
    ).json()
    for role in ("business", "technical", "security", "operations"):
        client.post(
            f"/api/v1/governance/observations/{window_id}/signoffs",
            headers=_headers(f"observation-signoff-{role}", updated["row_version"]),
            json={"role": role, "decision": "APPROVE", "comment": "synthetic"},
        )
    immediate = client.post(
        f"/api/v1/governance/observations/{window_id}:evaluate",
        headers=_headers("observation-evaluate", updated["row_version"]),
    ).json()
    close = client.post(
        f"/api/v1/governance/observations/{window_id}:close",
        headers=_headers("observation-close", updated["row_version"]),
    )
    final_report = client.get(
        f"/api/v1/governance/observations/{window_id}/final-acceptance-report"
    ).json()

    assert immediate["state"] == "BLOCKED"
    assert "OBSERVATION_NOT_CLOSED" in immediate["blockers"]
    assert "OBSERVATION_SEVEN_DAYS_NOT_ELAPSED" in immediate["blockers"]
    assert close.status_code == 409
    assert final_report["status"] == "BLOCKED"
    assert set(FINAL_REAL_EVIDENCE_REQUIREMENTS).issubset(final_report["blockers"])
