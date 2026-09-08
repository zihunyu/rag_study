from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from test_lifecycle_fact_source import _components, _upload

PASSWORD = "Fixture-only password 2026!"  # noqa: S105 - isolated synthetic accounts
ORIGIN = "http://testserver"


def login(app, username="admin", password=PASSWORD):
    client = TestClient(app)
    csrf = client.get("/api/auth/csrf")
    assert csrf.status_code == 200, csrf.text
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": csrf.json()["csrf_token"]})
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    for key, value in {
        "APP_ENV": "testing",
        "RAG_RUNTIME_PROFILE": "local",
        "VECTOR_BACKEND": "local",
        "AUTH_MODE": "password",
        "REAL_PROVIDER_CALLS_ENABLED": "false",
        "OCR_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    runtime = _components(tmp_path)
    runtime.accounts.bootstrap("admin", PASSWORD)
    app = create_app(runtime)
    return runtime, app, login(app)


def add_user(app, admin, username):
    response = admin.post("/api/admin/users", json={"username": username, "display_name": username})
    assert response.status_code == 201, response.text
    data = response.json()
    client = login(app, username, data["temporary_password"])
    assert client.get("/api/auth/me").json()["must_change_password"]
    assert client.get("/api/spaces").status_code == 403
    changed = client.post(
        "/api/auth/password",
        json={"current_password": data["temporary_password"], "new_password": PASSWORD},
    )
    assert changed.status_code == 200, changed.text
    return data["user"]["id"], login(app, username)


def assign(admin, space, user, role):
    state = admin.get(f"/api/spaces/{space}/members")
    assert state.status_code == 200, state.text
    result = admin.put(
        f"/api/spaces/{space}/members",
        headers={"If-Match": state.headers["etag"]},
        json={"changes": [{"user_id": user, "role": role}]},
    )
    assert result.status_code == 200, result.text
    return result


def test_anonymous_and_login_cookie(accounts):
    _, app, admin = accounts
    anonymous = TestClient(app)
    for path in ["/api/spaces", "/api/spaces/overview", "/api/system/status", "/docs"]:
        assert anonymous.get(path).status_code == 401
    assert "HttpOnly" in admin.get("/api/auth/csrf").headers["set-cookie"]
    assert admin.get("/api/auth/me").json()["global_role"] == "super_admin"
    assert anonymous.get("/health/live").status_code == 200


def test_csrf_and_session_rotation(accounts):
    _, app, admin = accounts
    old = admin.cookies.get("ragkb_session")
    response = admin.post("/api/spaces", headers={"X-CSRF-Token": "bad"}, json={"name": "forged"})
    assert response.status_code == 403
    assert (
        admin.post(
            "/api/spaces", headers={"Origin": "https://attacker.invalid"}, json={"name": "forged"}
        ).status_code
        == 403
    )
    assert admin.post("/api/auth/logout").status_code == 200
    replay = TestClient(app)
    replay.cookies.set("ragkb_session", old)
    assert replay.get("/api/auth/me").status_code == 401


def test_knowledge_base_role_is_scoped(accounts):
    runtime, app, admin = accounts
    aid = runtime.space_id
    bid = admin.post("/api/spaces", json={"name": "B"}).json()["id"]
    cid = admin.post("/api/spaces", json={"name": "C"}).json()["id"]
    uid, mixed = add_user(app, admin, "mixed")
    assign(admin, aid, uid, "manager")
    assign(admin, bid, uid, "qa")
    items = mixed.get("/api/spaces/overview").json()["items"]
    assert {s["id"]: s["my_role"] for s in items} == {aid: "manager", bid: "qa"}
    assert "pending_count" not in next(s for s in items if s["id"] == bid)
    assert mixed.get(f"/api/spaces/{aid}/documents/preview").status_code == 200
    for library in [bid, cid]:
        assert mixed.get(f"/api/spaces/{library}/documents/preview").status_code == 404
        assert mixed.patch(f"/api/spaces/{library}", json={"name": "bad"}).status_code == 404
    assert mixed.post("/api/spaces", json={"name": "bad"}).status_code == 403
    assert mixed.get("/api/system/status").status_code == 403
    assert mixed.get("/api/admin/users").status_code == 403
    assert (
        mixed.post(
            "/api/conversations", headers={"Idempotency-Key": "b"}, json={"space_id": bid}
        ).status_code
        == 200
    )
    assert (
        mixed.post(
            "/api/conversations", headers={"Idempotency-Key": "c"}, json={"space_id": cid}
        ).status_code
        == 404
    )


def test_manager_can_only_assign_qa(accounts):
    runtime, app, admin = accounts
    mid, manager = add_user(app, admin, "manager")
    rid, _ = add_user(app, admin, "reader")
    assign(admin, runtime.space_id, mid, "manager")
    assign(manager, runtime.space_id, rid, "qa")
    state = manager.get(f"/api/spaces/{runtime.space_id}/members")
    for uid, role in [(rid, "manager"), (mid, "none")]:
        response = manager.put(
            f"/api/spaces/{runtime.space_id}/members",
            headers={"If-Match": state.headers["etag"]},
            json={"changes": [{"user_id": uid, "role": role}]},
        )
        assert response.status_code == 403, response.text


def test_reader_cannot_read_documents_or_tasks(accounts):
    runtime, app, admin = accounts
    document, version, job = _upload(admin, runtime.space_id)
    uid, reader = add_user(app, admin, "reader")
    assign(admin, runtime.space_id, uid, "qa")
    for path in [
        f"/api/spaces/{runtime.space_id}/documents",
        f"/api/documents/{document}",
        f"/api/documents/{document}/preview",
        f"/api/document-versions/{version}/chunks",
        f"/api/document-versions/{version}/original/preview",
        f"/api/document-versions/{version}/visuals",
        f"/api/ingestion-jobs/{job}",
    ]:
        assert reader.get(path).status_code == 404, path
    assert reader.get("/api/ingestion-jobs").status_code == 403
    assert (
        reader.post("/api/search", json={"space_id": runtime.space_id, "query": "q"}).status_code
        == 404
    )


def test_revocation_hides_history_and_invalidates_captured_identity(accounts):
    runtime, app, admin = accounts
    uid, reader = add_user(app, admin, "reader")
    assign(admin, runtime.space_id, uid, "qa")
    conversation = reader.post(
        "/api/conversations",
        headers={"Idempotency-Key": "conv"},
        json={"space_id": runtime.space_id},
    ).json()
    old = runtime.accounts.authenticate(reader.cookies.get("ragkb_session"))
    assert runtime.accounts.recheck(uid, old.scope_tokens, (runtime.space_id,))
    assign(admin, runtime.space_id, uid, "none")
    assert reader.get("/api/conversations").json() == []
    assert reader.get("/api/conversations/" + conversation["id"]).status_code == 404
    assert not runtime.accounts.recheck(uid, old.scope_tokens, (runtime.space_id,))
    assign(admin, runtime.space_id, uid, "qa")
    assert not runtime.accounts.recheck(uid, old.scope_tokens, (runtime.space_id,))


def test_last_super_and_reset(accounts):
    runtime, app, admin = accounts
    current = admin.get("/api/auth/me").json()
    result = admin.patch(
        "/api/admin/users/" + current["id"], headers={"If-Match": '"1"'}, json={"enabled": False}
    )
    assert result.status_code == 409
    uid, reader = add_user(app, admin, "reader")
    row = runtime.accounts.user(uid)
    reset = admin.post(
        f"/api/admin/users/{uid}:reset-password", headers={"If-Match": str(row["row_version"])}
    )
    assert reset.status_code == 200
    assert reader.get("/api/auth/me").status_code == 401
    assert (
        login(app, "reader", reset.json()["temporary_password"])
        .get("/api/auth/me")
        .json()["must_change_password"]
    )


def test_soft_delete_restore_and_idempotency(accounts):
    runtime, app, admin = accounts
    uid, manager = add_user(app, admin, "manager")
    assign(admin, runtime.space_id, uid, "manager")
    state = manager.get(f"/api/spaces/{runtime.space_id}/members")
    headers = {"If-Match": state.headers["etag"], "Idempotency-Key": "delete-once"}
    first = manager.post(f"/api/spaces/{runtime.space_id}:delete", headers=headers)
    assert first.status_code == 200, first.text
    assert manager.get("/api/spaces/overview").json()["items"] == []
    assert manager.get(f"/api/spaces/{runtime.space_id}/documents").status_code == 404
    second = manager.post(f"/api/spaces/{runtime.space_id}:delete", headers=headers)
    assert second.json() == first.json()
    assert len(manager.get("/api/spaces/overview?include_deleted=true").json()["items"]) == 1
    restored = manager.post(
        f"/api/spaces/{runtime.space_id}:restore",
        headers={"If-Match": str(first.json()["row_version"]), "Idempotency-Key": "restore"},
    )
    assert restored.status_code == 200


def test_stale_membership_write_does_not_overwrite(accounts):
    runtime, app, admin = accounts
    uid, _ = add_user(app, admin, "reader")
    assign(admin, runtime.space_id, uid, "qa")
    rejected = admin.put(
        f"/api/spaces/{runtime.space_id}/members",
        headers={"If-Match": '"1"'},
        json={"changes": [{"user_id": uid, "role": "none"}]},
    )
    assert rejected.status_code == 412
    assert runtime.accounts.memberships(uid)[0]["role"] == "qa"


def test_passwords_are_hashes_and_expiry_is_enforced(accounts):
    runtime, app, admin = accounts
    uid, reader = add_user(app, admin, "reader")
    row = runtime.accounts.user(uid)
    assert row["password_hash"].startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert PASSWORD not in row["password_hash"]
    with runtime.accounts.db.transaction() as connection:
        runtime.accounts.db.execute(
            connection,
            "UPDATE auth_sessions SET last_seen_at=? WHERE user_id=?",
            (time.time() - 1801, uid),
        )
    assert reader.get("/api/auth/me").status_code == 401


def test_administrator_audit_is_explicit_and_logged(accounts):
    runtime, app, admin = accounts
    uid, reader = add_user(app, admin, "reader")
    assign(admin, runtime.space_id, uid, "qa")
    conversation = reader.post(
        "/api/conversations",
        headers={"Idempotency-Key": "audit"},
        json={"space_id": runtime.space_id, "title": "private title"},
    ).json()
    assert admin.get("/api/conversations/" + conversation["id"]).status_code == 404
    index = admin.get("/api/admin/conversation-audits").json()["items"]
    assert "title" not in index[0]
    result = admin.post(
        f"/api/admin/conversation-audits/{conversation['id']}:read",
        json={"reason": "Review reported answer quality"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["conversation"]["title"] == "private title"
    events = admin.get("/api/admin/audit-events").json()["items"]
    assert any(e["action"] == "conversation.audit_read" for e in events)


def answered_runtime(runtime, admin, generator=None):
    import threading
    from dataclasses import replace

    from ragkb.adapters.rag_stubs import (
        DeterministicBufferedGenerator,
        StaticFinalPermission,
        SyntheticEvidenceProvider,
    )
    from ragkb.application.qa import TrustedQAService
    from ragkb.domain.rag import Evidence
    from ragkb.infrastructure.account_qa import (
        AccountEvidenceProvider,
        AccountFinalPermission,
        account_release_guard,
    )
    from test_lifecycle_fact_source import _process_next

    document, version, _ = _upload(admin, runtime.space_id)
    _process_next(runtime, admin, version)
    assert (
        admin.post(
            f"/api/document-versions/{version}:publish", headers={"Idempotency-Key": "pub"}
        ).status_code
        == 200
    )
    evidence = Evidence(
        "E1",
        "fixture-chunk",
        document,
        version,
        "设备保修期为三年。",
        {"page": 2, "internal_notes": "private management metadata"},
        0,
        0,
        10,
        1,
        True,
        True,
    )
    qa = TrustedQAService(
        AccountEvidenceProvider(
            runtime.accounts, SyntheticEvidenceProvider((evidence,)), runtime.space_id
        ),
        generator or DeterministicBufferedGenerator(),
        AccountFinalPermission(runtime.accounts, StaticFinalPermission()),
        runtime.reference_signer,
        runtime.rag_repository,
        response_release_guard=lambda: account_release_guard(runtime.accounts, threading.RLock()),
    )
    return replace(runtime, qa_service=qa), document


def test_actual_reader_answer_sources_are_private_and_revocable(accounts):
    runtime, app, admin = accounts
    uid, _ = add_user(app, admin, "reader")
    _, other = add_user(app, admin, "other")
    assign(admin, runtime.space_id, uid, "qa")
    runtime, document = answered_runtime(runtime, admin)
    reader = login(create_app(runtime), "reader")
    response = reader.post(
        "/api/ask", json={"question": "保修多久？", "space_id": runtime.space_id}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["verified"] and result["answer"]
    assert "internal_notes" not in str(result["citations"])
    url = result["citations"][0]["source_url"]
    source = reader.get(url)
    assert source.status_code == 200, source.text
    assert source.json()["text"] == "设备保修期为三年。"
    assert "internal_notes" not in source.text
    assert other.get(url).status_code == 404
    assert reader.get(f"/api/documents/{document}").status_code == 404
    assign(admin, runtime.space_id, uid, "none")
    assert reader.get(url).status_code == 404
    assert (
        reader.post(
            "/api/ask", json={"question": "保修多久？", "space_id": runtime.space_id}
        ).status_code
        == 404
    )


def test_revoking_during_generation_never_releases_answer(accounts):
    from ragkb.adapters.rag_stubs import DeterministicBufferedGenerator

    runtime, app, admin = accounts
    uid, _ = add_user(app, admin, "reader")
    assign(admin, runtime.space_id, uid, "qa")

    class RevokingGenerator(DeterministicBufferedGenerator):
        def generate(self, *args, **kwargs):
            result = super().generate(*args, **kwargs)
            assign(admin, runtime.space_id, uid, "none")
            return result

    runtime, _ = answered_runtime(runtime, admin, RevokingGenerator())
    reader = login(create_app(runtime), "reader")
    response = reader.post(
        "/api/ask:stream", json={"question": "保修多久？", "space_id": runtime.space_id}
    )
    assert "设备保修期为三年" not in response.text
    assert "event: result" not in response.text
    assert "KNOWLEDGE_BASE_ACCESS_REVOKED" in response.text


def test_sensitive_bytes_rechecked_after_handler_returns(accounts):
    runtime, _, _ = accounts
    app = create_app(runtime)

    @app.get("/api/spaces/{space_id}/late-response")
    def late_response(space_id: str):
        import subprocess
        import sys

        code = """
import sys
from pathlib import Path
from ragkb.infrastructure.sqlite import SQLiteDatabase
from ragkb.infrastructure.workspace_db import WorkspaceDB
from ragkb.infrastructure.account_lock import permission_guard
db = WorkspaceDB(SQLiteDatabase(Path(sys.argv[1])))
with permission_guard(db, sys.argv[2]), db.transaction() as c:
    db.execute(c, 'UPDATE auth_users SET auth_revision=auth_revision+1 WHERE tenant_id=?',
               (sys.argv[2],))
"""
        subprocess.run(  # noqa: S603 - isolated fixture subprocess
            [sys.executable, "-c", code, str(runtime.accounts.db.database.path), runtime.tenant_id],
            check=True,
            timeout=15,
            capture_output=True,
        )  # noqa: S603
        return {"secret": "must-never-reach-client"}

    admin = login(app)
    response = admin.get(f"/api/spaces/{runtime.space_id}/late-response")
    assert response.status_code == 403, response.text
    assert "must-never-reach-client" not in response.text


def test_expired_temporary_password_and_login_errors_are_uniform(accounts):
    runtime, app, admin = accounts
    data = admin.post(
        "/api/admin/users", json={"username": "expiry", "display_name": "Expiry"}
    ).json()
    with runtime.accounts.db.transaction() as c:
        runtime.accounts.db.execute(
            c,
            "UPDATE auth_users SET password_expires_at=? WHERE id=?",
            (time.time() - 1, data["user"]["id"]),
        )
    client = TestClient(app)
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": csrf})
    replies = [
        client.post("/api/auth/login", json={"username": u, "password": p})
        for u, p in [
            ("expiry", data["temporary_password"]),
            ("absent", PASSWORD),
            ("admin", "wrong"),
        ]
    ]
    assert {r.status_code for r in replies} == {401}
    assert len({r.json()["message"] for r in replies}) == 1


def test_membership_batch_rolls_back_entire_invalid_change(accounts):
    runtime, app, admin = accounts
    mid, manager = add_user(app, admin, "manager")
    rid, _ = add_user(app, admin, "reader")
    assign(admin, runtime.space_id, mid, "manager")
    state = manager.get(f"/api/spaces/{runtime.space_id}/members")
    response = manager.put(
        f"/api/spaces/{runtime.space_id}/members",
        headers={"If-Match": state.headers["etag"]},
        json={"changes": [{"user_id": rid, "role": "qa"}, {"user_id": mid, "role": "none"}]},
    )
    assert response.status_code == 403
    assert runtime.accounts.memberships(rid) == []


def test_cookie_absolute_expiry_and_periodic_me_do_not_extend_idle(accounts):
    runtime, app, admin = accounts
    uid, reader = add_user(app, admin, "reader")
    with runtime.accounts.db.transaction() as c:
        runtime.accounts.db.execute(
            c, "UPDATE auth_sessions SET last_seen_at=? WHERE user_id=?", (100, uid)
        )
    assert reader.get("/api/auth/me").status_code == 401
    reader = login(app, "reader")
    before = runtime.accounts.session(reader.cookies.get("ragkb_session"))["last_seen_at"]
    reader.get("/api/auth/me")
    assert runtime.accounts.session(reader.cookies.get("ragkb_session"))["last_seen_at"] == before
    with runtime.accounts.db.transaction() as c:
        runtime.accounts.db.execute(
            c, "UPDATE auth_sessions SET expires_at=? WHERE user_id=?", (time.time() - 1, uid)
        )
    assert reader.get("/api/auth/me").status_code == 401


def test_password_openapi_and_minimal_image_receipt(accounts):
    from ragkb.api.citation_projection import cited_asset_ids

    _, app, _ = accounts
    schema = app.openapi()
    assert schema["paths"]["/api/ask"]["post"]["security"] == [{"SessionCookie": []}]
    assert schema["paths"]["/api/auth/login"]["post"]["security"] == []
    assert any(
        p["name"] == "X-CSRF-Token"
        for p in schema["paths"]["/api/auth/login"]["post"]["parameters"]
    )
    assert cited_asset_ids(
        {"visual_asset_ids": ["a", "b"], "used_visual_fact_ids": ["v:a:0:node:n"]}
    ) == ["a"]
    assert cited_asset_ids({"visual_asset_ids": ["a"], "used_visual_fact_ids": []}) == []


def test_real_login_limiter_path_reports_retry_after(accounts, monkeypatch):
    from dataclasses import replace

    from ragkb.adapters.redis_cache import RedisCacheRateLimitAdapter

    runtime, _, _ = accounts
    calls = []

    def deny(self, *args, **kwargs):
        calls.append((args, kwargs))
        return False

    monkeypatch.setattr(RedisCacheRateLimitAdapter, "allow", deny)
    app = create_app(
        replace(runtime, settings=runtime.settings.model_copy(update={"app_env": "development"}))
    )
    client = TestClient(app)
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    )
    assert response.status_code == 429, response.text
    assert response.headers["retry-after"] == "900"
    assert len(calls) == 2


def test_library_usage_does_not_disclose_other_conversation_content(accounts):
    runtime, app, admin = accounts
    mid, manager = add_user(app, admin, "manager")
    assign(admin, runtime.space_id, mid, "manager")
    data = manager.get(f"/api/spaces/{runtime.space_id}/usage")
    assert data.status_code == 200, data.text
    assert data.json()["conversation_count"] == 0
    assert "turns" not in data.json()


def test_only_actually_cited_image_is_exposed_without_management_data(accounts):
    from dataclasses import replace

    from ragkb.domain.rag import Citation
    from ragkb.infrastructure.visual_assets import VisualAssetStore
    from test_visual_pipeline import png

    runtime, app, admin = accounts
    uid, _ = add_user(app, admin, "reader")
    assign(admin, runtime.space_id, uid, "qa")
    runtime, document = answered_runtime(runtime, admin)
    reader = login(create_app(runtime), "reader")
    answer = reader.post(
        "/api/ask", json={"question": "保修多久？", "space_id": runtime.space_id}
    ).json()
    package = runtime.rag_repository.get_package(answer["rag_run_id"])
    result = runtime.rag_repository.get_result(answer["rag_run_id"])
    version = package.evidence[0].document_version_id
    store = VisualAssetStore(runtime.storage)
    assets = [
        {
            **store.save_image(version, png(), {"page": i}),
            "status": "verified",
            "history": [{"comment": "private review history"}],
            "regions": [],
        }
        for i in [1, 2]
    ]
    store.write_manifest(version, assets)
    locator = {
        "visual_asset_ids": [a["id"] for a in assets],
        "page": 1,
        "used_visual_fact_ids": [f"{version}:{assets[0]['id']}:0:note:1"],
    }
    evidence = replace(package.evidence[0], locator=locator)
    run_id = "account-visual-fixture"
    url = runtime.reference_signer.source_url(run_id, "E1", runtime.tenant_id, uid, document)
    runtime.rag_repository.save_run(
        replace(package, rag_run_id=run_id, evidence=(evidence,)),
        replace(
            result,
            rag_run_id=run_id,
            evidence=(evidence,),
            citations=(Citation("E1", url, locator),),
        ),
    )
    source = reader.get(url)
    assert source.status_code == 200, source.text
    visuals = source.json()["visuals"]
    assert len(visuals) == 1 and visuals[0]["id"] == assets[0]["id"]
    assert "history" not in visuals[0] and "extraction" not in visuals[0]
    assert reader.get(visuals[0]["image_url"]).content == png()
    unrelated = visuals[0]["image_url"].replace(assets[0]["id"], assets[1]["id"])
    assert reader.get(unrelated).status_code == 404
    assert (
        reader.get(f"/api/document-versions/{version}/visuals/{assets[0]['id']}/image").status_code
        == 404
    )


def test_manager_publish_withdraw_then_library_restore_does_not_republish(accounts):
    from test_lifecycle_fact_source import _process_next

    runtime, app, admin = accounts
    uid, manager = add_user(app, admin, "manager")
    rid, reader = add_user(app, admin, "reader")
    assign(admin, runtime.space_id, uid, "manager")
    assign(manager, runtime.space_id, rid, "qa")
    document, version, _ = _upload(manager, runtime.space_id)
    _process_next(runtime, manager, version)
    assert (
        manager.post(
            f"/api/document-versions/{version}:publish", headers={"Idempotency-Key": "pub"}
        ).status_code
        == 200
    )
    assert (
        manager.post(
            f"/api/documents/{document}:revoke", headers={"Idempotency-Key": "revoke"}
        ).status_code
        == 200
    )
    before = runtime.lifecycle_store.documents[document].lifecycle_state
    state = manager.get(f"/api/spaces/{runtime.space_id}/members")
    deleted = manager.post(
        f"/api/spaces/{runtime.space_id}:delete",
        headers={"If-Match": state.headers["etag"], "Idempotency-Key": "del"},
    )
    assert reader.get("/api/auth/me").json()["spaces"] == []
    assert (
        manager.post(
            f"/api/spaces/{runtime.space_id}:restore",
            headers={"If-Match": str(deleted.json()["row_version"]), "Idempotency-Key": "restore"},
        ).status_code
        == 200
    )
    assert runtime.lifecycle_store.documents[document].lifecycle_state == before
    assert not runtime.lifecycle_store.documents[document].visible


def test_uninitialized_second_admin_cannot_bypass_last_effective_admin_protection(accounts):
    runtime, _, admin = accounts
    second = admin.post(
        "/api/admin/users",
        json={"username": "temporary-super", "display_name": "Temp", "global_role": "super_admin"},
    )
    assert second.status_code == 201
    identity = admin.get("/api/auth/me").json()
    response = admin.patch(
        "/api/admin/users/" + identity["id"],
        headers={"If-Match": str(identity["row_version"])},
        json={"global_role": "member"},
    )
    assert response.status_code == 409


def test_reader_coverage_reports_cannot_list_uncited_material():
    from ragkb.api.citation_projection import reader_report

    raw = {
        "mode": "overview",
        "complete": False,
        "read_sections": 1,
        "source_documents": [{"filename": "uncited.docx"}],
        "sections": [{"section": "private section"}],
        "image_checks": [{"status": "supported", "transcript": "uncited text"}],
        "gaps": ["private section was unread"],
        "conditions": {"checked": 2, "source_quote": "private quote"},
    }
    public = reader_report(raw)
    assert public["read_sections"] == 1
    assert "uncited" not in str(public) and "private" not in str(public)
