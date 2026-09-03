"""Dry-run fixture glyph/render scan and deterministic rebuild planning."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from ragkb.evaluation.fixture_glyph_coverage import glyph_coverage


def fixture_rebuild_plan(fixtures: Sequence[Mapping[str, object]]) -> dict[str, object]:
    records = []
    for fixture in fixtures:
        fixture_id = str(fixture["fixture_id"])
        text = str(fixture["text"])
        payload = bytes(fixture["payload"])
        coverage = glyph_coverage(text, fixture["supported_codepoints"])
        records.append(
            {
                "fixture_id": fixture_id,
                "source_sha256": hashlib.sha256(payload, usedforsecurity=False).hexdigest(),
                "rebuild_required": coverage["covered"] is False,
                "missing_codepoint_count": coverage["missing_codepoint_count"],
            }
        )
    return {"revision": "fixture-rebuild-plan:v1", "dry_run": True, "records": records}
