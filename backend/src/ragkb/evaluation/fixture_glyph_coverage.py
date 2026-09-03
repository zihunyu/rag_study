"""Content-neutral glyph coverage and render-proof utilities for fixtures."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence


def glyph_coverage(text: str, supported_codepoints: Sequence[int]) -> dict[str, object]:
    supported = set(supported_codepoints)
    missing = sorted({ord(character) for character in text if not character.isspace()} - supported)
    return {
        "text_sha256": hashlib.sha256(text.encode(), usedforsecurity=False).hexdigest(),
        "missing_codepoint_count": len(missing),
        "missing_codepoints": missing,
        "covered": not missing,
    }


def select_font_fallback(text: str, candidates: Mapping[str, Sequence[int]]) -> str:
    for name in sorted(candidates):
        if glyph_coverage(text, candidates[name])["covered"] is True:
            return name
    raise ValueError("FIXTURE_GLYPH_COVERAGE_UNSATISFIED")
