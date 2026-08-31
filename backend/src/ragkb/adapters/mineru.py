"""Self-hosted/hosted MinerU routing without binding to a supplier SDK."""

from __future__ import annotations

from dataclasses import dataclass


class MinerURouteDenied(PermissionError):
    """Raised when hosted parsing would violate the data-egress policy."""


@dataclass(frozen=True)
class MinerUEndpoint:
    kind: str
    base_url: str
    enabled: bool


class MinerURouter:
    def __init__(
        self,
        self_hosted: MinerUEndpoint,
        hosted: MinerUEndpoint,
        hosted_allowed_classifications: frozenset[str],
    ) -> None:
        self.self_hosted = self_hosted
        self.hosted = hosted
        self.hosted_allowed_classifications = hosted_allowed_classifications

    def select(
        self,
        classification: str,
        *,
        self_hosted_available: bool = True,
        provider_region_approved: bool = False,
    ) -> MinerUEndpoint:
        if self_hosted_available and self.self_hosted.enabled:
            return self.self_hosted
        hosted_allowed = classification in self.hosted_allowed_classifications
        if self.hosted.enabled and hosted_allowed and provider_region_approved:
            return self.hosted
        raise MinerURouteDenied("hosted MinerU is disabled or disallowed for this data class")
