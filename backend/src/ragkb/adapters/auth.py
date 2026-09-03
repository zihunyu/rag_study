"""Local development identity and offline-testable OIDC/JWT claim validation."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
import jwt

from ragkb.config import EnvSettings
from ragkb.domain.auth import RequestPrincipal


class AuthenticationError(RuntimeError):
    pass


class AuthorizationError(RuntimeError):
    pass


class LocalSingleUserAuthenticator:
    revision = "local-single-user-auth:g3-dev-v1"

    def __init__(self, settings: EnvSettings, *, tenant_id: str | None = None) -> None:
        self.settings = settings
        self.tenant_id = tenant_id or settings.auth_local_tenant

    def authenticate(self, authorization_header: str | None) -> RequestPrincipal:
        return RequestPrincipal(
            tenant_id=self.tenant_id,
            user_id=self.settings.auth_local_user_id,
            roles=("admin", "knowledge_maintainer", "reader"),
            scope_tokens=(
                f"tenant:{self.tenant_id}",
                f"user:{self.settings.auth_local_user_id}",
                "role:admin",
            ),
            auth_mode="local_single_user",
            clearance_level=3,
        )


class OIDCJWTAuthenticator:
    revision = "oidc-jwt-auth:g3-v1"

    def __init__(
        self,
        settings: EnvSettings,
        *,
        verified_decoder: Callable[[str, str, str], Mapping[str, Any]],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.verified_decoder = verified_decoder
        self.clock = clock

    def authenticate(self, authorization_header: str | None) -> RequestPrincipal:
        if not authorization_header or not authorization_header.startswith("Bearer "):
            raise AuthenticationError("AUTH_BEARER_REQUIRED")
        bearer_value = authorization_header[7:].strip()
        if not bearer_value:
            raise AuthenticationError("AUTH_BEARER_REQUIRED")
        try:
            claims = self.verified_decoder(
                bearer_value,
                self.settings.oidc_issuer_url,
                self.settings.oidc_audience,
            )
        except Exception as error:
            raise AuthenticationError("AUTH_TOKEN_INVALID") from error
        if (
            claims.get("iss") != self.settings.oidc_issuer_url
            or self.settings.oidc_audience not in _audiences(claims.get("aud"))
            or int(claims.get("exp", 0)) <= int(self.clock())
            or not claims.get("sub")
            or not claims.get("tenant_id")
        ):
            raise AuthenticationError("AUTH_TOKEN_CLAIMS_INVALID")
        roles = tuple(sorted(set(map(str, claims.get("roles", [])))))
        groups = tuple(sorted(set(map(str, claims.get("groups", [])))))
        tenant_id = str(claims["tenant_id"])
        user_id = str(claims["sub"])
        clearance_value = claims.get(self.settings.oidc_clearance_claim)
        if clearance_value is None:
            raise AuthenticationError("AUTH_CLEARANCE_CLAIM_REQUIRED")
        try:
            clearance_level = int(clearance_value)
        except (TypeError, ValueError) as error:
            raise AuthenticationError("AUTH_CLEARANCE_CLAIM_INVALID") from error
        if clearance_level < 0 or clearance_level > 3:
            raise AuthenticationError("AUTH_CLEARANCE_CLAIM_INVALID")
        return RequestPrincipal(
            tenant_id=tenant_id,
            user_id=user_id,
            roles=roles,
            scope_tokens=(
                f"tenant:{tenant_id}",
                f"user:{user_id}",
                *(f"role:{role}" for role in roles),
                *(f"group:{group}" for group in groups),
            ),
            auth_mode="oidc",
            clearance_level=clearance_level,
        )


def _audiences(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(map(str, value))
    return ()


def unavailable_oidc_decoder(token: str, issuer: str, audience: str) -> Mapping[str, Any]:
    raise AuthenticationError("OIDC_VERIFIED_DECODER_NOT_CONFIGURED")


class OIDCDiscoveryJWTDecoder:
    """HTTPS discovery and cached JWKS verification for production bearer tokens."""

    revision = "oidc-discovery-jwks-pyjwt:v1"

    def __init__(self, settings: EnvSettings) -> None:
        self.settings = settings
        self._client = httpx.Client(timeout=settings.oidc_discovery_timeout_seconds)
        self._lock = threading.Lock()
        self._jwks: dict[str, Any] = {}
        self._expires_at = 0.0

    def close(self) -> None:
        self._client.close()

    def _refresh(self, issuer: str) -> None:
        response = self._client.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration")
        response.raise_for_status()
        discovery = response.json()
        if not isinstance(discovery, Mapping) or discovery.get("issuer") != issuer:
            raise AuthenticationError("OIDC_DISCOVERY_ISSUER_INVALID")
        jwks_uri = discovery.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri.startswith("https://"):
            raise AuthenticationError("OIDC_JWKS_URI_INVALID")
        jwks_response = self._client.get(jwks_uri)
        jwks_response.raise_for_status()
        document = jwks_response.json()
        keys = document.get("keys") if isinstance(document, Mapping) else None
        if not isinstance(keys, list) or not keys:
            raise AuthenticationError("OIDC_JWKS_INVALID")
        parsed: dict[str, Any] = {}
        for item in keys:
            if not isinstance(item, dict) or not isinstance(item.get("kid"), str):
                continue
            parsed[item["kid"]] = jwt.PyJWK.from_dict(item).key
        if not parsed:
            raise AuthenticationError("OIDC_JWKS_INVALID")
        self._jwks = parsed
        self._expires_at = time.monotonic() + self.settings.oidc_jwks_cache_seconds

    def __call__(self, token: str, issuer: str, audience: str) -> Mapping[str, Any]:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm not in self.settings.oidc_allowed_algorithms or not isinstance(key_id, str):
            raise AuthenticationError("OIDC_TOKEN_HEADER_INVALID")
        with self._lock:
            if time.monotonic() >= self._expires_at or key_id not in self._jwks:
                self._refresh(issuer)
            key = self._jwks.get(key_id)
        if key is None:
            raise AuthenticationError("OIDC_SIGNING_KEY_NOT_FOUND")
        claims = jwt.decode(
            token,
            key=key,
            algorithms=list(self.settings.oidc_allowed_algorithms),
            audience=audience,
            issuer=issuer,
            leeway=self.settings.oidc_clock_skew_seconds,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
        if not isinstance(claims, Mapping):
            raise AuthenticationError("OIDC_TOKEN_CLAIMS_INVALID")
        return claims
