"""The same publication policy applies to every persistence profile."""

from collections.abc import Mapping
from typing import Any


def review_quality_error(
    quality: Mapping[str, Any] | None,
    review: Mapping[str, Any] | None,
) -> str | None:
    if quality is None:
        return "PUBLICATION_QUALITY_REPORT_MISSING"
    if quality.get("disposition") == "BLOCKED_REAL_VALIDATION":
        return "PUBLICATION_QUALITY_BLOCKED_REAL_VALIDATION"
    if review is None:
        return "PUBLICATION_REVIEW_REQUIRED"
    if review.get("decision") != "APPROVED":
        return "PUBLICATION_REVIEW_NOT_APPROVED"
    if not review.get("security_revision") or not (
        review.get("security_projection") or review.get("security_projection_json")
    ):
        return "PUBLICATION_SECURITY_REVIEW_REQUIRED"
    if review.get("quality_revision") != quality.get("parser_revision"):
        return "PUBLICATION_REVIEW_REVISION_MISMATCH"
    if review.get("projection_applied") is not None and not review["projection_applied"]:
        return "PUBLICATION_SECURITY_PROJECTION_PENDING"
    return None
