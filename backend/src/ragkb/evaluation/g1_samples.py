"""Prepare and validate ignored real-sample landing directories."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import jsonschema
import yaml

G1_FORMATS = (
    "pdf_text",
    "pdf_scanned_or_image",
    "docx",
    "pptx",
    "spreadsheet",
)


def _load_mapping(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"expected mapping: {path}")
    return loaded


def _resolve(root: Path, configured: object) -> Path:
    path = Path(str(configured))
    return path if path.is_absolute() else (root / path).resolve()


def _g1_plan(root: Path, plan_path: Path) -> list[Mapping[str, Any]]:
    plan = _load_mapping(plan_path)
    items = plan.get("collection_plan", [])
    if not isinstance(items, list):
        raise ValueError("collection_plan must be a list")
    return [item for item in items if isinstance(item, Mapping) and item.get("real_gate") == "G1"]


def prepare_g1_sample_landing(root: Path, plan_path: Path) -> list[Path]:
    created: list[Path] = []
    for item in _g1_plan(root, plan_path):
        directory = _resolve(root, item["sample_directory"])
        metadata = _resolve(root, item["metadata_path"])
        directory.mkdir(parents=True, exist_ok=True)
        if not metadata.exists():
            metadata.write_text(
                yaml.safe_dump(
                    {
                        "schema_version": 1,
                        "format": str(item["format"]),
                        "real_gate": "G1",
                        "samples": [],
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        created.append(directory)
    return created


def check_g1_samples(
    root: Path, plan_path: Path, schema_path: Path | None = None
) -> dict[str, Any]:
    resolved_schema = schema_path or (
        Path(__file__).resolve().parents[1] / "contracts/schemas/g1-sample-metadata-v1.schema.json"
    )
    schema = json.loads(resolved_schema.read_text(encoding="utf-8"))
    by_format: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for item in _g1_plan(root, plan_path):
        format_name = str(item["format"])
        required = int(item["required_count"])
        directory = _resolve(root, item["sample_directory"])
        metadata_path = _resolve(root, item["metadata_path"])
        if not metadata_path.is_file():
            blockers.append(f"{format_name}:metadata_missing")
            count = 0
        else:
            metadata = _load_mapping(metadata_path)
            try:
                jsonschema.Draft202012Validator(schema).validate(metadata)
            except jsonschema.ValidationError as error:
                blockers.append(f"{format_name}:metadata_invalid:{error.json_path}")
                count = 0
            else:
                samples = metadata["samples"]
                valid = 0
                for sample in samples:
                    sample_path = (directory / str(sample["file"])).resolve()
                    try:
                        sample_path.relative_to(directory.resolve())
                    except ValueError:
                        blockers.append(f"{format_name}:{sample['id']}:path_escape")
                        continue
                    if not sample_path.is_file():
                        blockers.append(f"{format_name}:{sample['id']}:file_missing")
                        continue
                    valid += 1
                count = valid
        if count < required:
            blockers.append(f"{format_name}:need_{required}_found_{count}")
        by_format[format_name] = {"required": required, "valid_real_samples": count}
    return {
        "gate": "G1",
        "ready": not blockers,
        "real_acceptance": not blockers,
        "by_format": by_format,
        "blockers": sorted(set(blockers)),
    }
