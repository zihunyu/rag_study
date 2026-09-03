from __future__ import annotations

from pathlib import Path

import yaml
from ragkb.evaluation.fixture_manifest_scan import scan_fixture_manifest


def test_generated_manifest_enumeration_is_content_free(tmp_path: Path) -> None:
    data = tmp_path / "fixtures"
    data.mkdir()
    source = data / "generated.bin"
    source.write_bytes(b"\x00\x01\x02")
    metadata = {"samples": [{"file": "generated.bin", "expected_locators": [{"page": 1}]}]}
    (tmp_path / "metadata.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    manifest = {
        "collection_plan": [
            {
                "format": "generated",
                "metadata_path": "metadata.yaml",
                "sample_directory": "fixtures",
            }
        ]
    }
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    report = scan_fixture_manifest(tmp_path, manifest_path)
    assert report["fixture_count"] == 1
    assert report["records"][0]["locator_count"] == 1
    assert report["records"][0]["representation_status"] == "UNAVAILABLE_FAIL_CLOSED"
    assert "000102" not in str(report)
