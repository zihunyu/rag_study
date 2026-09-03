"""Authenticated request principal contract."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestPrincipal:
    tenant_id: str
    user_id: str
    roles: tuple[str, ...]
    scope_tokens: tuple[str, ...]
    auth_mode: str

    def has_role(self, *roles: str) -> bool:
        return bool(set(self.roles).intersection(roles))
