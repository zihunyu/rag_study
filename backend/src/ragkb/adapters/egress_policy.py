"""Fail-closed policy for third-party AI use in the local development baseline."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass


@dataclass(frozen=True)
class EgressDecision:
    allowed: bool
    reason: str


def decide_external_ai_egress(
    *,
    classification: str,
    outbound_ai_allowed: bool | str,
    allowed_classifications: Collection[str],
    provider_region_approved: bool,
    cross_border_transfer_allowed: bool,
    provider_is_cross_border: bool,
) -> EgressDecision:
    """Apply classification, provider-region and cross-border controls."""

    normalized_classification = classification.casefold()
    normalized_allowed = {item.casefold() for item in allowed_classifications}
    if normalized_classification == "restricted":
        return EgressDecision(False, "restricted_data_outbound_forbidden")
    if outbound_ai_allowed is not True:
        return EgressDecision(False, "outbound_ai_disabled")
    if normalized_classification not in normalized_allowed:
        return EgressDecision(False, "classification_not_approved")
    if not provider_region_approved:
        return EgressDecision(False, "provider_processing_region_not_approved")
    if provider_is_cross_border and not cross_border_transfer_allowed:
        return EgressDecision(False, "cross_border_transfer_forbidden")
    return EgressDecision(True, "classification_and_region_approved")
