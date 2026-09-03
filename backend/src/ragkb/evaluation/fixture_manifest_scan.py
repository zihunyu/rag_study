"""Manifest-driven, content-free fixture enumeration for render coverage gates."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from ragkb.evaluation.fixture_source_representation import representation


def scan_fixture_manifest(root: Path, manifest_path: Path) -> dict[str, object]:
    plan = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    records = []
    for item in plan["collection_plan"]:
        if item.get("deferred_by_user") is True:
            records.append({"category": item["format"], "representation_status": "DEFERRED_BY_USER"})
            continue
        metadata_path = root / item["metadata_path"]
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        for sample in metadata["samples"]:
            source = root / item["sample_directory"] / sample["file"]
            record = {
                    "category": item["format"],
                    "fixture_ref": str(source.relative_to(root)),
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "locator_count": len(sample.get("expected_locators", [])),
                    "representation_status": "UNAVAILABLE_FAIL_CLOSED",
            }
            locators = sample.get("expected_locators", [])
            if locators:
                proof = representation(item["format"], source, locators[0])
                record.update(representation_status=proof["status"], locator_sha256=proof["locator_sha256"], representation_sha256=proof["representation_sha256"])
            records.append(record)
    return {
        "revision": "fixture-manifest-scan:v1",
        "fixture_count": len(records),
        "records": records,
        "provider_call_count": 0,
        "content_output": False,
    }
