"""Write a content-free dynamic future UAT retest preflight report."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.evaluation.uat_retest_source_builder import build_retest_source_status  # noqa: E402


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(),
        usedforsecurity=False,
    ).hexdigest()


def _normal_ref(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("UAT_RETEST_FIXTURE_REF_INVALID")
    return value.replace("\\", "/")


def _confirmation_report(root: Path, scan_path: Path) -> dict[str, object]:
    confirmation_paths = sorted(root.glob("*确认.jsonl"))
    if len(confirmation_paths) != 1:
        raise ValueError("UAT_RETEST_CONFIRMATION_JSONL_INVALID")
    rows = [
        json.loads(line)
        for line in confirmation_paths[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not all(isinstance(row, Mapping) for row in rows):
        raise ValueError("UAT_RETEST_CONFIRMATION_ROW_INVALID")
    with (root / "source_mapping.csv").open(encoding="utf-8-sig", newline="") as handle:
        mapping_rows = list(csv.DictReader(handle))
    mapping_by_index = {str(row.get("review_index")): row for row in mapping_rows}
    input_cases: list[dict[str, object]] = []
    for path in sorted((root / "input_cases").glob("*.json")):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("UAT_RETEST_INPUT_CASE_INVALID")
        input_cases.append(loaded)
    input_by_index = {str(row.get("review_index")): row for row in input_cases}
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    raw_scan_records = scan.get("records") if isinstance(scan, Mapping) else None
    if not isinstance(raw_scan_records, list):
        raise ValueError("UAT_RETEST_SCAN_REPORT_INVALID")
    scan_by_ref = {
        _normal_ref(record.get("fixture_ref")): record
        for record in raw_scan_records
        if isinstance(record, Mapping) and record.get("fixture_ref") is not None
    }
    records: list[dict[str, object]] = []
    for row in rows:
        assert isinstance(row, Mapping)
        index = str(row.get("review_index"))
        mapped = mapping_by_index.get(index)
        case = input_by_index.get(index)
        if mapped is None or case is None:
            raise ValueError("UAT_RETEST_CONFIRMATION_JOIN_MISSING")
        bundle_sha = case.get("source_bundle_sha256")
        if bundle_sha != row.get("source_bundle_sha256_from_case"):
            raise ValueError("UAT_RETEST_CONFIRMATION_BUNDLE_MISMATCH")
        fixture_ref = _normal_ref(mapped.get("source_file"))
        scan_record = scan_by_ref.get(fixture_ref)
        available = (
            isinstance(scan_record, Mapping)
            and scan_record.get("representation_status") == "AVAILABLE"
        )
        source_confirmed = (
            row.get("facts_consistent") is True
            and row.get("structure_consistent") is True
            and row.get("source_fixture_valid") is True
        )
        state = "ELIGIBLE" if available and source_confirmed else "BLOCKED"
        records.append(
            {
                "test_case_id": row.get("test_case_id"),
                "source_bundle_sha256": bundle_sha,
                "fixture_ref": fixture_ref,
                "source_category": mapped.get("source_category"),
                "source_sha256": mapped.get("source_sha256"),
                "locator_sha256": _canonical_hash(row.get("expected_locator")),
                "state": state,
                "reason_code": (
                    "READY_FOR_FRESH_SOURCE_BUILD"
                    if state == "ELIGIBLE"
                    else "SOURCE_OR_REPRESENTATION_NOT_CONFIRMED"
                ),
                "provider_call_count": 0,
            }
        )
    return {
        "revision": "uat-retest-source-preflight:v5",
        "selected_count": len(records),
        "eligible_count": sum(record["state"] == "ELIGIBLE" for record in records),
        "blocked_count": sum(record["state"] == "BLOCKED" for record in records),
        "records": records,
        "provider_call_count": 0,
        "content_output": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("review", type=Path, nargs="?")
    parser.add_argument("scan", type=Path, nargs="?")
    parser.add_argument("cases", type=Path, nargs="?")
    parser.add_argument("--confirmation-root", type=Path)
    parser.add_argument("--scan-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.confirmation_root is not None:
        scan_path = args.scan_report or args.scan or args.review
        if scan_path is None:
            raise SystemExit("SCAN_REQUIRED")
        report = _confirmation_report(args.confirmation_root, scan_path)
    elif args.review is not None and args.scan is not None and args.cases is not None:
        review = [
            json.loads(line)
            for line in args.review.read_text(encoding="utf-8").splitlines()
            if line
        ]
        scan = {
            _normal_ref(record["fixture_ref"]): record
            for record in json.loads(args.scan.read_text(encoding="utf-8"))["records"]
            if record.get("fixture_ref")
        }
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
        records = build_retest_source_status(review, scan, cases)
        report = {
            "revision": "uat-retest-source-preflight:v5",
            "selected_count": len(records),
            "eligible_count": sum(record["state"] == "ELIGIBLE" for record in records),
            "blocked_count": sum(record["state"] == "BLOCKED" for record in records),
            "records": records,
            "provider_call_count": 0,
            "content_output": False,
        }
    else:
        raise SystemExit("CONFIRMATION_ROOT_OR_LEGACY_INPUTS_REQUIRED")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
