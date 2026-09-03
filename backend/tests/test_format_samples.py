from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from ragkb.evaluation.format_samples import check_format_samples, prepare_format_sample_landing


def _plan(root: Path) -> Path:
    plan = root / "plan.yaml"
    entries = []
    for name in (
        "pdf_text",
        "pdf_scanned_or_image",
        "docx",
        "pptx",
        "spreadsheet",
        "audio",
    ):
        entries.append(
            {
                "format": name,
                "required_count": 1,
                "owner_role": "qa_evaluation",
                "real_gate": "G4",
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
    paths = prepare_format_sample_landing(tmp_path, plan)

    assert len(paths) == 6
    assert all(path.is_dir() for path in paths)
    assert all(
        yaml.safe_load((path / "metadata.yaml").read_text(encoding="utf-8"))["samples"] == []
        for path in paths
    )
    report = check_format_samples(tmp_path, plan)
    assert report["gate"] == "G4"
    assert report["ready"] is False
    assert len(report["blockers"]) == 6


def test_checker_counts_only_existing_files_with_locator_metadata(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    paths = prepare_format_sample_landing(tmp_path, plan)
    for path in paths:
        sample = path / "real.bin"
        sample.write_bytes(b"real fixture placeholder for checker test")
        metadata = yaml.safe_load((path / "metadata.yaml").read_text(encoding="utf-8"))
        metadata["samples"] = [
            {
                "id": f"{path.name}-1",
                "file": sample.name,
                "real_sample": True,
                "sha256": hashlib.sha256(sample.read_bytes()).hexdigest(),
                "deidentified": True,
                "authorization_ref": "fixture-authorization",
                "rights_confirmed": True,
                "source_classification": "internal",
                "expected_locators": [{"page": 1}],
            }
        ]
        (path / "metadata.yaml").write_text(
            yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8"
        )

    report = check_format_samples(tmp_path, plan)
    assert report["ready"] is True
    assert report["real_acceptance"] is True


def test_checker_rejects_missing_authorization_or_checksum_mismatch(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    paths = prepare_format_sample_landing(tmp_path, plan)
    path = paths[0]
    sample = path / "unapproved.bin"
    sample.write_bytes(b"synthetic stand-in")
    metadata = yaml.safe_load((path / "metadata.yaml").read_text(encoding="utf-8"))
    metadata["samples"] = [
        {
            "id": "missing-authorization",
            "file": sample.name,
            "real_sample": True,
            "sha256": "0" * 64,
            "deidentified": True,
            "rights_confirmed": True,
            "source_classification": "internal",
            "expected_locators": [{"page": 1}],
        }
    ]
    (path / "metadata.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

    report = check_format_samples(tmp_path, plan)

    assert report["ready"] is False
    assert any("metadata_invalid" in blocker for blocker in report["blockers"])


def test_audio_deferred_is_excluded_from_current_non_asr_scope(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    payload = yaml.safe_load(plan.read_text(encoding="utf-8"))
    for item in payload["collection_plan"]:
        if item["format"] == "audio":
            item["deferred_by_user"] = True
            item["acquisition_status"] = "deferred_by_user"
    plan.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    report = check_format_samples(tmp_path, plan)

    assert report["scope"] == {
        "original_full_scope": "6x1",
        "original_required_count": 6,
        "current_non_asr_scope": "5x1",
        "current_required_count": 5,
        "audio_deferred": True,
    }
    assert report["by_format"]["audio"]["status"] == "deferred_by_user"
    assert not any(blocker.startswith("audio:") for blocker in report["blockers"])
