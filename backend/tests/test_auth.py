from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from ragkb.adapters.auth import (
    AuthenticationError,
    OIDCDiscoveryJWTDecoder,
    OIDCJWTAuthenticator,
)
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
        "clearance_level": 2,
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


def test_api_uses_oidc_principal_for_401_403_and_tenant_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("RAG_RUNTIME_PROFILE", "local")
    monkeypatch.setenv("AUTH_MODE", "local_single_user")
    monkeypatch.setenv("REAL_PROVIDER_CALLS_ENABLED", "false")
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


def test_production_oidc_decoder_discovers_and_caches_jwks(tmp_path: Path) -> None:
    settings = _oidc_settings(tmp_path)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "key-1", "alg": "RS256", "use": "sig"})
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": settings.oidc_issuer_url,
                    "jwks_uri": "https://issuer.example/.well-known/jwks.json",
                },
                request=request,
            )
        return httpx.Response(200, json={"keys": [public_jwk]}, request=request)

    decoder = OIDCDiscoveryJWTDecoder(settings)
    decoder._client.close()
    decoder._client = httpx.Client(transport=httpx.MockTransport(handler))
    token = jwt.encode(
        _claims("tenant-1", ["reader"]),
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )

    assert decoder(token, settings.oidc_issuer_url, settings.oidc_audience)["sub"] == "oidc-user"
    assert decoder(token, settings.oidc_issuer_url, settings.oidc_audience)["sub"] == "oidc-user"
    assert len(requests) == 2
    within_skew = jwt.encode(
        {**_claims("tenant-1", ["reader"]), "nbf": int(time.time()) + 30},
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    assert decoder(within_skew, settings.oidc_issuer_url, settings.oidc_audience)["sub"]
    beyond_skew = jwt.encode(
        {**_claims("tenant-1", ["reader"]), "nbf": int(time.time()) + 120},
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    with pytest.raises(jwt.ImmatureSignatureError):
        decoder(beyond_skew, settings.oidc_issuer_url, settings.oidc_audience)
    decoder.close()


def test_oidc_unknown_kid_refreshes_jwks_for_key_rotation(tmp_path: Path) -> None:
    settings = _oidc_settings(tmp_path)
    first_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    second_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    first_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(first_key.public_key()))
    second_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(second_key.public_key()))
    first_jwk.update({"kid": "key-1", "alg": "RS256", "use": "sig"})
    second_jwk.update({"kid": "key-2", "alg": "RS256", "use": "sig"})
    current_keys = [[first_jwk]]
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": settings.oidc_issuer_url,
                    "jwks_uri": "https://issuer.example/.well-known/jwks.json",
                },
                request=request,
            )
        return httpx.Response(200, json={"keys": current_keys[0]}, request=request)

    decoder = OIDCDiscoveryJWTDecoder(settings)
    decoder._client.close()
    decoder._client = httpx.Client(transport=httpx.MockTransport(handler))
    first_token = jwt.encode(
        _claims("tenant-1", ["reader"]),
        first_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    assert decoder(first_token, settings.oidc_issuer_url, settings.oidc_audience)["sub"]
    current_keys[0] = [second_jwk]
    second_token = jwt.encode(
        _claims("tenant-1", ["reader"]),
        second_key,
        algorithm="RS256",
        headers={"kid": "key-2"},
    )

    assert decoder(second_token, settings.oidc_issuer_url, settings.oidc_audience)["sub"]
    assert len(requests) == 4
    decoder.close()
