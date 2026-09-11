"""Repeat-level quality and cost accounting; retries never count as extra samples."""

from __future__ import annotations

from statistics import median
from typing import Any


def _stats(values: list[float]) -> dict[str, Any]:
    return {
        "measured": len(values),
        "median_seconds": round(median(values), 4) if values else None,
        "worst_seconds": round(max(values), 4) if values else None,
    }


def summarize_repeats(run: dict[str, Any]) -> dict[str, Any]:
    planned = run["payload"].get("repeat_count", 1)
    rows, samples = [], []
    for case in run["payload"]["cases"]:
        case_samples = []
        for repetition in range(1, planned + 1):
            attempts = [
                a
                for a in run.get("attempts", [])
                if a["case_id"] == case["id"] and a["payload"].get("repetition", 1) == repetition
            ]
            last = attempts[-1] if attempts else {}
            payload = last.get("payload", {})
            terminal = last.get("state") in {"completed", "failed"}
            result = (payload.get("steps") or [{}])[-1].get("result", {})
            status = result.get("status", "not_run")
            group = (
                "running"
                if last.get("state") == "running"
                else status
                if status in {"answered", "insufficient_evidence", "conflicting_evidence"}
                else "other"
                if terminal and status in {"needs_clarification", "out_of_scope"}
                else "system_failure"
                if attempts
                else "not_run"
            )
            checks = payload.get("checks", [])
            mechanical = terminal and bool(checks) and all(c["passed"] for c in checks)
            points = last.get("point_results", payload.get("point_results", []))
            point_failure = any(p["status"] in {"missing", "incorrect"} for p in points)
            model_points = payload.get("point_results", [])
            assisted = bool(model_points) and all(
                p["status"] == "covered" and p.get("origin") == "model" for p in model_points
            )
            human = bool(last.get("reviews")) and last.get("verdict") == "passed"
            # Count active QA time spent before recovery too. A recovered review
            # reuses the saved QA time and must not count that same receipt twice.
            durations = []
            unknown = False
            for attempt in attempts:
                p = attempt["payload"]
                if p.get("reused_qa_receipt"):
                    continue
                duration = p.get("qa_elapsed_seconds")
                if duration is None and p.get("phase") != "semantic_review":
                    duration = p.get("elapsed_seconds")
                if duration is None:
                    unknown = True
                else:
                    durations.append(float(duration))
            seconds = sum(durations) if durations and not unknown else None
            sample = {
                "repetition": repetition,
                "verdict": last.get("verdict", "incomplete"),
                "attempt_ids": [a["id"] for a in attempts],
                "group": group,
                "status": status,
                "complete": terminal,
                "mechanical_passed": mechanical,
                "assisted_passed": assisted,
                "human_passed": human,
                "quality_failed": point_failure or (terminal and not mechanical),
                "seconds": round(seconds, 4) if seconds is not None else None,
                "retries": max(0, len(attempts) - 1),
            }
            case_samples.append(sample)
        samples.extend(case_samples)
        rows.append(
            {
                "case_id": case["id"],
                "key": case["payload"]["key"],
                "planned": planned,
                "completed": sum(s["complete"] for s in case_samples),
                "mechanical_passed": sum(s["mechanical_passed"] for s in case_samples),
                "assisted_passed": sum(s["assisted_passed"] for s in case_samples),
                "human_passed": sum(s["human_passed"] for s in case_samples),
                "samples": case_samples,
                **_stats([s["seconds"] for s in case_samples if s["seconds"] is not None]),
            }
        )
    groups = [
        {
            "group": name,
            "count": len(selected),
            **_stats([s["seconds"] for s in selected if s["seconds"] is not None]),
        }
        for name in (
            "answered",
            "insufficient_evidence",
            "conflicting_evidence",
            "running",
            "system_failure",
            "other",
            "not_run",
        )
        if (selected := [s for s in samples if s["group"] == name])
    ]
    return {"rows": rows, "groups": groups, "planned_samples": len(samples)}


def compare_repeats(
    before: dict[str, Any], after: dict[str, Any], quality_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    old, new = summarize_repeats(before), summarize_repeats(after)
    old_rows = {r["case_id"]: r for r in old["rows"]}
    quality = {r["case_id"]: r for r in quality_rows}
    a, b = before["payload"]["snapshot"], after["payload"]["snapshot"]
    differences = [k for k in sorted(a.keys() | b.keys()) if a.get(k) != b.get(k)]
    rows = []
    for right in new["rows"]:
        left = old_rows.get(right["case_id"])
        reason = "not_comparable"
        delta = None
        if left and quality.get(right["case_id"], {}).get("change") in {
            "pending",
            "unchanged",
            "improved",
            "regressed",
        }:
            comparable = left["planned"] == right["planned"]
            complete = all(r["completed"] == r["planned"] == r["measured"] for r in (left, right))
            statuses = [sorted(s["group"] for s in r["samples"]) for r in (left, right)]
            # Code/model changes are the experiment, but remain explicit. Source,
            # criteria, permissions and reviewer mismatches invalidate attribution.
            if not comparable:
                reason = "repeat_count_changed"
            elif not complete:
                reason = "incomplete"
            elif statuses[0] != statuses[1]:
                reason = "outcome_changed"
            elif any(s["quality_failed"] for s in right["samples"]):
                reason = "quality_failed"
            elif not all(s["human_passed"] for r in (left, right) for s in r["samples"]):
                reason = "human_review_pending"
            else:
                reason = "quality_verified"
            if comparable and complete and statuses[0] == statuses[1] and left["median_seconds"]:
                delta = round((right["median_seconds"] / left["median_seconds"] - 1) * 100, 2)
        rows.append(
            {
                "case_id": right["case_id"],
                "key": right["key"],
                "before": left,
                "after": right,
                "assessment": reason,
                "median_change_percent": delta,
            }
        )
    return {
        "rows": rows,
        "snapshot_differences": differences,
        "before_groups": old["groups"],
        "after_groups": new["groups"],
    }
