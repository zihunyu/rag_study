from __future__ import annotations

from pathlib import Path

import yaml
from ragkb.evaluation.g1_samples import check_g1_samples, prepare_g1_sample_landing


def _plan(root: Path) -> Path:
    plan = root / "plan.yaml"
    entries = []
    for name in ("pdf_text", "pdf_scanned_or_image", "docx", "pptx", "spreadsheet"):
        entries.append(
            {
                "format": name,
                "required_count": 1,
                "owner_role": "qa_evaluation",
                "real_gate": "G1",
                "acquisition_status": "pending",
                "sample_directory": f"samples/{name}",
                "metadata_path": f"samples/{name}/metadata.yaml",
            }
        )
    plan.write_text(
        yaml.safe_dump({"schema_version": 1, "collection_plan": entries}),
        encoding="utf-8",
    )
    return plan


def test_prepare_creates_empty_ignored_style_landing_without_fake_samples(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    paths = prepare_g1_sample_landing(tmp_path, plan)

    assert len(paths) == 5
    assert all(path.is_dir() for path in paths)
    assert all(
        yaml.safe_load((path / "metadata.yaml").read_text(encoding="utf-8"))["samples"] == []
        for path in paths
    )
    report = check_g1_samples(tmp_path, plan)
    assert report["ready"] is False
    assert len(report["blockers"]) == 5


def test_checker_counts_only_existing_files_with_locator_metadata(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    paths = prepare_g1_sample_landing(tmp_path, plan)
    for path in paths:
        sample = path / "real.bin"
        sample.write_bytes(b"real fixture placeholder for checker test")
        metadata = yaml.safe_load((path / "metadata.yaml").read_text(encoding="utf-8"))
        metadata["samples"] = [
            {
                "id": f"{path.name}-1",
                "file": sample.name,
                "real_sample": True,
                "expected_locators": [{"page": 1}],
            }
        ]
        (path / "metadata.yaml").write_text(
            yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8"
        )

    report = check_g1_samples(tmp_path, plan)
    assert report["ready"] is True
    assert report["real_acceptance"] is True
