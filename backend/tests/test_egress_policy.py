from __future__ import annotations

import pytest
from ragkb.adapters.egress_policy import decide_external_ai_egress

ALLOWED = {"public", "internal", "confidential"}


@pytest.mark.parametrize("classification", ["public", "internal", "confidential"])
def test_approved_classifications_require_provider_region(classification: str) -> None:
    pending = decide_external_ai_egress(
        classification=classification,
        outbound_ai_allowed=True,
        allowed_classifications=ALLOWED,
        provider_region_approved=False,
        cross_border_transfer_allowed=False,
        provider_is_cross_border=False,
    )
    approved = decide_external_ai_egress(
        classification=classification,
        outbound_ai_allowed=True,
        allowed_classifications=ALLOWED,
        provider_region_approved=True,
        cross_border_transfer_allowed=False,
        provider_is_cross_border=False,
    )
    assert pending.allowed is False
    assert approved.allowed is True


def test_restricted_and_cross_border_are_denied() -> None:
    restricted = decide_external_ai_egress(
        classification="restricted",
        outbound_ai_allowed=True,
        allowed_classifications={*ALLOWED, "restricted"},
        provider_region_approved=True,
        cross_border_transfer_allowed=True,
        provider_is_cross_border=False,
    )
    cross_border = decide_external_ai_egress(
        classification="confidential",
        outbound_ai_allowed=True,
        allowed_classifications=ALLOWED,
        provider_region_approved=True,
        cross_border_transfer_allowed=False,
        provider_is_cross_border=True,
    )
    assert restricted.allowed is False
    assert cross_border.allowed is False
