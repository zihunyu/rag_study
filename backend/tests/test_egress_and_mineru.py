from __future__ import annotations

import pytest
from ragkb.adapters.egress_policy import decide_external_ai_egress
from ragkb.adapters.mineru import MinerUEndpoint, MinerURouteDenied, MinerURouter

ALLOWED = {"public", "internal", "confidential"}


@pytest.mark.parametrize("classification", ["public", "internal", "confidential"])
def test_approved_classifications_require_approved_provider_region(
    classification: str,
) -> None:
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


def test_restricted_and_cross_border_egress_are_denied() -> None:
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


def test_mineru_self_hosted_primary_and_hosted_policy() -> None:
    router = MinerURouter(
        MinerUEndpoint("self_hosted", "http://127.0.0.1:8000", True),
        MinerUEndpoint("hosted", "https://mineru.net/api/v4", True),
        frozenset(ALLOWED),
    )

    assert router.select("confidential").kind == "self_hosted"
    with pytest.raises(MinerURouteDenied):
        router.select("confidential", self_hosted_available=False)
    assert (
        router.select(
            "confidential", self_hosted_available=False, provider_region_approved=True
        ).kind
        == "hosted"
    )
    with pytest.raises(MinerURouteDenied):
        router.select("restricted", self_hosted_available=False, provider_region_approved=True)
