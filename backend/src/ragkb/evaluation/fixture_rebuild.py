"""Dry-run fixture glyph/render scan and deterministic rebuild planning."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import cast

from ragkb.evaluation.fixture_glyph_coverage import glyph_coverage


def fixture_rebuild_plan(fixtures: Sequence[Mapping[str, object]]) -> dict[str, object]:
    records = []
    for fixture in fixtures:
        fixture_id = str(fixture["fixture_id"])
        text = str(fixture["text"])
        raw_payload = fixture["payload"]
        if not isinstance(raw_payload, (bytes, bytearray)):
            raise TypeError("fixture payload must be bytes")
        payload = bytes(raw_payload)
        supported = fixture["supported_codepoints"]
        if not isinstance(supported, Sequence) or isinstance(supported, (str, bytes)):
            raise TypeError("supported codepoints must be a sequence")
        coverage = glyph_coverage(text, cast(Sequence[int], supported))
        records.append(
            {
                "fixture_id": fixture_id,
                "source_sha256": hashlib.sha256(payload, usedforsecurity=False).hexdigest(),
                "rebuild_required": coverage["covered"] is False,
                "missing_codepoint_count": coverage["missing_codepoint_count"],
            }
        )
    return {"revision": "fixture-rebuild-plan:v1", "dry_run": True, "records": records}
