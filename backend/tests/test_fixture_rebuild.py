from __future__ import annotations

from ragkb.evaluation.fixture_rebuild import fixture_rebuild_plan


def test_dry_run_marks_only_generated_fixture_with_missing_glyph() -> None:
    clean = b"clean-payload"
    flagged = b"flagged-payload"
    plan = fixture_rebuild_plan(
        [
            {"fixture_id": "fixture-a", "text": "A", "payload": clean, "supported_codepoints": [0x41]},
            {"fixture_id": "fixture-b", "text": "AB", "payload": flagged, "supported_codepoints": [0x41]},
        ]
    )
    assert plan["dry_run"] is True
    assert [record["rebuild_required"] for record in plan["records"]] == [False, True]
    assert clean == b"clean-payload"
    assert flagged == b"flagged-payload"
