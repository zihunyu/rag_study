"""Content-free dynamic fresh-source eligibility builder for future UAT retests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def build_retest_source_status(
    review_rows: Sequence[Mapping[str, object]],
    scan_records: Mapping[str, Mapping[str, object]],
    cases: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    selected = sorted(
        str(r["candidate_id"])
        for r in review_rows
        if r.get("type") == "candidate_review" and r.get("audit_verdict") in {"不通过", "待修订"}
    )
    records = []
    for case_id in selected:
        case = cases[case_id]
        fixture_ref = str(case["fixture_ref"])
        scan = scan_records.get(fixture_ref)
        status = (
            "ELIGIBLE"
            if scan
            and scan.get("representation_status") == "AVAILABLE"
            and case.get("source_integrity") is True
            else "BLOCKED"
        )
        records.append(
            {
                "test_case_id": case_id,
                "fixture_ref": fixture_ref,
                "state": status,
                "provider_call_count": 0,
                "question_sha256": case.get("question_sha256"),
            }
        )
    return records
