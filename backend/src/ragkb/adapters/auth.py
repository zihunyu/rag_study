"""Local development identity and offline-testable OIDC/JWT claim validation."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

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
        )


def _audiences(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(map(str, value))
    return ()


def unavailable_oidc_decoder(token: str, issuer: str, audience: str) -> Mapping[str, Any]:
    raise AuthenticationError("OIDC_VERIFIED_DECODER_NOT_CONFIGURED")
