from __future__ import annotations

import pytest
from ragkb.evaluation.fixture_glyph_coverage import glyph_coverage, select_font_fallback


def test_generated_glyph_coverage_and_fallback_are_deterministic() -> None:
    text = "".join(chr(value) for value in (0x41, 0x3A9, 0x4E00))
    first = glyph_coverage(text, (0x41, 0x3A9))
    assert first["covered"] is False
    assert first["missing_codepoint_count"] == 1
    fallback = select_font_fallback(text, {"font-a": (0x41,), "font-b": (0x41, 0x3A9, 0x4E00)})
    assert fallback == "font-b"
    assert glyph_coverage(text, (0x41, 0x3A9, 0x4E00))["covered"] is True


def test_glyph_gate_rejects_when_no_candidate_covers_generated_text() -> None:
    with pytest.raises(ValueError, match="COVERAGE_UNSATISFIED"):
        select_font_fallback("".join(chr(value) for value in (0x61, 0x62)), {"font": (0x61,)})
