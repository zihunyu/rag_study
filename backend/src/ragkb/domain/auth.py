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
    clearance_level: int = 0

    def __post_init__(self) -> None:
        if self.clearance_level < 0 or self.clearance_level > 3:
            raise ValueError("clearance level must be in 0..3")

    def has_role(self, *roles: str) -> bool:
        return bool(set(self.roles).intersection(roles))
