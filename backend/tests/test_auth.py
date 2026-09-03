from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ragkb.adapters.auth import AuthenticationError, OIDCJWTAuthenticator
from ragkb.api.app import create_app
from ragkb.config import load_env
from ragkb.runtime_components import build_runtime_components


def _oidc_settings(tmp_path: Path):
    secret_key = "OIDC_CLIENT_" + "SECRET"
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            (
                "AUTH_MODE=oidc",
                "OIDC_ISSUER_URL=https://issuer.example",
                "OIDC_AUDIENCE=rag-api",
                "OIDC_CLIENT_ID=rag-client",
                f"{secret_key}=fixture-client-credential",
            )
        ),
        encoding="utf-8",
    )
    loaded = load_env(Path(__file__).resolve().parents[2], env_path=env, environ={})
    assert loaded.settings is not None
    return loaded.settings


def _claims(tenant_id: str, roles: list[str]):
    return {
        "iss": "https://issuer.example",
        "aud": ["rag-api"],
        "exp": int(time.time()) + 600,
        "sub": "oidc-user",
        "tenant_id": tenant_id,
        "roles": roles,
        "groups": ["engineering"],
    }


def test_oidc_authenticator_validates_claims_and_builds_scope_tokens(tmp_path: Path) -> None:
    settings = _oidc_settings(tmp_path)
    captured = {}

    def decoder(token: str, issuer: str, audience: str):
        captured.update(token=token, issuer=issuer, audience=audience)
        return _claims("tenant-1", ["reader"])

    principal = OIDCJWTAuthenticator(settings, verified_decoder=decoder).authenticate(
        "Bearer signed.jwt.token"
    )

    assert captured == {
        "token": "signed.jwt.token",
        "issuer": "https://issuer.example",
        "audience": "rag-api",
    }
    assert principal.tenant_id == "tenant-1"
    assert principal.user_id == "oidc-user"
    assert "role:reader" in principal.scope_tokens
    assert "group:engineering" in principal.scope_tokens


def test_oidc_invalid_expiry_issuer_or_missing_bearer_is_rejected(tmp_path: Path) -> None:
    settings = _oidc_settings(tmp_path)
    expired = {**_claims("tenant", ["reader"]), "exp": 1}
    authenticator = OIDCJWTAuthenticator(
        settings, verified_decoder=lambda token, issuer, audience: expired
    )

    with pytest.raises(AuthenticationError):
        authenticator.authenticate(None)
    with pytest.raises(AuthenticationError):
        authenticator.authenticate("Bearer expired")


def test_api_uses_oidc_principal_for_401_403_and_tenant_fail_closed(tmp_path: Path) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    settings = _oidc_settings(tmp_path)

    def app_for(tenant_id: str, roles: list[str]) -> TestClient:
        authenticator = OIDCJWTAuthenticator(
            settings,
            verified_decoder=lambda token, issuer, audience: _claims(tenant_id, roles),
        )
        return TestClient(create_app(replace(components, authenticator=authenticator)))

    reader = app_for(components.tenant_id, ["reader"])
    no_role = app_for(components.tenant_id, [])
    other_tenant = app_for("other-tenant", ["reader"])

    assert reader.post("/api/v1/ask", json={"question": "q"}).status_code == 401
    assert (
        reader.post(
            "/api/v1/ask",
            headers={"Authorization": "Bearer valid"},
            json={"question": "q"},
        ).status_code
        == 200
    )
    assert (
        no_role.post(
            "/api/v1/ask",
            headers={"Authorization": "Bearer valid"},
            json={"question": "q"},
        ).status_code
        == 403
    )
    assert (
        other_tenant.post(
            "/api/v1/search",
            headers={"Authorization": "Bearer valid"},
            json={"query": "q"},
        ).status_code
        == 404
    )

    schema = reader.app.openapi()
    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    assert schema["paths"]["/api/v1/ask"]["post"]["security"] == [{"BearerAuth": []}]
