from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from ragkb.evaluation.local_sample_validation import (
    _expected_locator_match,
    external_call_plan,
    validate_local_samples,
)


def test_local_sample_report_is_anonymous_content_free_and_zero_external_calls(
    tmp_path: Path,
) -> None:
    sample_dir = tmp_path / "samples"
    sample_dir.mkdir()
    sample = sample_dir / "sensitive-name.txt"
    sample.write_text("private synthetic fixture", encoding="utf-8")
    metadata = sample_dir / "metadata.yaml"
    metadata.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "format": "pdf_text",
                "real_gate": "G4",
                "samples": [
                    {
                        "id": "private-id",
                        "file": sample.name,
                        "real_sample": True,
                        "sha256": hashlib.sha256(sample.read_bytes()).hexdigest(),
                        "deidentified": True,
                        "authorization_ref": "authorized",
                        "rights_confirmed": True,
                        "source_classification": "internal",
                        "expected_locators": [{"char_range": [0, 25]}],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    plan = tmp_path / "plan.yaml"
    plan.write_text(
        yaml.safe_dump(
            {
                "collection_plan": [
                    {
                        "format": "pdf_text",
                        "real_gate": "G4",
                        "required_count": 1,
                        "sample_directory": str(sample_dir),
                        "metadata_path": str(metadata),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = validate_local_samples(Path(__file__).resolve().parents[2], plan)
    plan_report = external_call_plan(report)
    serialized = str(report)

    assert report["external_call_count"] == 0
    assert report["sample_content_emitted"] is False
    assert report["sample_filename_emitted"] is False
    assert "sensitive-name" not in serialized
    assert "private synthetic fixture" not in serialized
    assert plan_report["executed"] is False


def test_spreadsheet_expected_range_requires_complete_union_coverage() -> None:
    expected = [{"sheet": "Sheet1", "cell_range": "A1:C3"}]

    assert _expected_locator_match(
        expected, [{"sheet": "Sheet1", "cell_range": "A1:C1", "row": 1}]
    ) == (0, 1)
    assert _expected_locator_match(
        expected,
        [
            {"sheet": "Sheet1", "cell_range": "A1:C1", "row": 1},
            {"sheet": "Sheet1", "cell_range": "A3:C3", "row": 3},
        ],
    ) == (0, 1)
    assert _expected_locator_match(
        expected,
        [{"sheet": "Sheet1", "cell_range": f"A{row}:B{row}", "row": row} for row in range(1, 4)],
    ) == (0, 1)
    complete = [
        {"sheet": "Sheet1", "cell_range": f"A{row}:B{row}", "row": row} for row in range(1, 4)
    ] + [{"sheet": "Sheet1", "cell_range": f"C{row}:C{row}", "row": row} for row in range(1, 4)]
    assert _expected_locator_match(expected, complete) == (1, 1)
    assert _expected_locator_match(
        expected,
        [
            {"sheet": "WrongSheet", "cell_range": f"A{row}:C{row}", "row": row}
            for row in range(1, 4)
        ],
    ) == (0, 1)


def test_spreadsheet_row_locator_is_one_based_exact() -> None:
    assert _expected_locator_match([{"row": 2}], [{"row": 2}]) == (1, 1)
    assert _expected_locator_match([{"row": 2}], [{"row": 1}, {"row": 3}]) == (0, 1)
