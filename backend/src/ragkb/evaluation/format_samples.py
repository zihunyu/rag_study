"""Prepare and validate ignored G4 real-format sample landing directories."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import jsonschema
import yaml

FORMAT_SAMPLE_GATE = "G4"


def _load_mapping(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"expected mapping: {path}")
    return loaded


def _resolve(root: Path, configured: object) -> Path:
    path = Path(str(configured))
    return path if path.is_absolute() else (root / path).resolve()


def _all_g4_plan(root: Path, plan_path: Path) -> list[Mapping[str, Any]]:
    del root
    plan = _load_mapping(plan_path)
    items = plan.get("collection_plan", [])
    if not isinstance(items, list):
        raise ValueError("collection_plan must be a list")
    return [
        item
        for item in items
        if isinstance(item, Mapping) and item.get("real_gate") == FORMAT_SAMPLE_GATE
    ]


def _g4_plan(root: Path, plan_path: Path) -> list[Mapping[str, Any]]:
    return [item for item in _all_g4_plan(root, plan_path) if not item.get("deferred_by_user")]


def prepare_format_sample_landing(root: Path, plan_path: Path) -> list[Path]:
    created: list[Path] = []
    for item in _all_g4_plan(root, plan_path):
        directory = _resolve(root, item["sample_directory"])
        metadata = _resolve(root, item["metadata_path"])
        directory.mkdir(parents=True, exist_ok=True)
        if not metadata.exists():
            metadata.write_text(
                yaml.safe_dump(
                    {
                        "schema_version": 1,
                        "format": str(item["format"]),
                        "real_gate": FORMAT_SAMPLE_GATE,
                        "samples": [],
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        created.append(directory)
    return created


def check_format_samples(
    root: Path, plan_path: Path, schema_path: Path | None = None
) -> dict[str, Any]:
    resolved_schema = schema_path or (
        Path(__file__).resolve().parents[1]
        / "contracts/schemas/format-sample-metadata-v1.schema.json"
    )
    schema = json.loads(resolved_schema.read_text(encoding="utf-8"))
    by_format: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    all_items = _all_g4_plan(root, plan_path)
    active_items = _g4_plan(root, plan_path)
    for item in all_items:
        format_name = str(item["format"])
        required = int(item["required_count"])
        if item.get("deferred_by_user"):
            by_format[format_name] = {
                "required": required,
                "valid_real_samples": 0,
                "status": "deferred_by_user",
                "counted_in_current_scope": False,
            }
            continue
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
                    digest = hashlib.sha256(sample_path.read_bytes()).hexdigest()
                    if digest != str(sample["sha256"]).casefold():
                        blockers.append(f"{format_name}:{sample['id']}:checksum_mismatch")
                        continue
                    valid += 1
                count = valid
        if count < required:
            blockers.append(f"{format_name}:need_{required}_found_{count}")
        by_format[format_name] = {
            "required": required,
            "valid_real_samples": count,
            "status": "pending" if count < required else "ready",
            "counted_in_current_scope": True,
        }
    original_required = sum(int(item["required_count"]) for item in all_items)
    current_required = sum(int(item["required_count"]) for item in active_items)
    original_unit = int(all_items[0]["required_count"]) if all_items else 0
    current_unit = int(active_items[0]["required_count"]) if active_items else 0
    return {
        "gate": FORMAT_SAMPLE_GATE,
        "ready": not blockers,
        "real_acceptance": not blockers,
        "scope": {
            "original_full_scope": f"{len(all_items)}x{original_unit}",
            "original_required_count": original_required,
            "current_non_asr_scope": f"{len(active_items)}x{current_unit}",
            "current_required_count": current_required,
            "audio_deferred": any(
                item.get("format") == "audio" and item.get("deferred_by_user") for item in all_items
            ),
        },
        "by_format": by_format,
        "blockers": sorted(set(blockers)),
        "required_sample_fields": [
            "sha256",
            "deidentified",
            "authorization_ref",
            "rights_confirmed",
            "source_classification",
            "expected_locators",
        ],
    }
