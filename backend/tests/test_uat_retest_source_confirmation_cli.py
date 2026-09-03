from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_confirmation_root_cli_joins_by_index_and_bundle_hash(tmp_path: Path) -> None:
    root = tmp_path / "confirmation"
    cases = root / "input_cases"
    cases.mkdir(parents=True)
    (root / "确认.jsonl").write_text(
        json.dumps(
            {
                "review_index": 1,
                "test_case_id": "future-case-1",
                "source_bundle_sha256_from_case": "bundle-hash",
                "expected_locator": {"page": 1},
                "facts_consistent": True,
                "structure_consistent": True,
                "source_fixture_valid": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "source_mapping.csv").write_text(
        "review_index,source_category,source_file,source_sha256\n"
        "1,pdf_text,data/samples/example.pdf,source-hash\n",
        encoding="utf-8",
    )
    (cases / "01.json").write_text(
        json.dumps({"review_index": 1, "source_bundle_sha256": "bundle-hash"}),
        encoding="utf-8",
    )
    scan = tmp_path / "scan.json"
    scan.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "fixture_ref": "data/samples/example.pdf",
                        "representation_status": "AVAILABLE",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output.json"
    script = Path(__file__).resolve().parents[2] / "scripts/prepare_uat_retest_sources_v5.py"
    subprocess.run(  # noqa: S603 - fixed repository script under test
        [
            sys.executable,
            str(script),
            str(scan),
            "--confirmation-root",
            str(root),
            "--output",
            str(output),
        ],
        check=True,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["selected_count"] == report["eligible_count"] == 1
    assert report["records"][0]["fixture_ref"] == "data/samples/example.pdf"
