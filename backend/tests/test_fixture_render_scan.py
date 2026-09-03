from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from scan_fixture_render_coverage import scan  # noqa: E402


def test_generated_source_render_scan_is_deterministic_and_content_free(tmp_path: Path):
    source = tmp_path / "source.txt"
    render = tmp_path / "render.txt"
    source.write_text("AB", encoding="utf-8")
    render.write_text("AB", encoding="utf-8")
    pairs = [{"source": str(source), "rendered": str(render), "supported_codepoints": [65, 66]}]
    first = scan(pairs)
    assert scan(pairs) == first
    assert first["flagged_count"] == 0
    assert "AB" not in json.dumps(first)
    render.write_text("AC", encoding="utf-8")
    assert scan(pairs)["flagged_count"] == 1
