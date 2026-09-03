"""Authentication port for local and OIDC request contexts."""

from __future__ import annotations

from typing import Protocol

from ragkb.domain.auth import RequestPrincipal


class AuthenticatorPort(Protocol):
    revision: str

    def authenticate(self, authorization_header: str | None) -> RequestPrincipal: ...
