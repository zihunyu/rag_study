"""Verify that the generic UAT remediation contains no historical content literals."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "artifacts/user-review/uat-v4-package-20260902/UAT_v4_逐项审核结果.jsonl"
SOURCES = (
    ROOT / "backend/src/ragkb/evaluation/uat_generic_remediation.py",
    ROOT / "backend/src/ragkb/evaluation/uat_error_case_retest.py",
    ROOT / "backend/src/ragkb/application/uat_future_claim_runner.py",
    ROOT / "backend/src/ragkb/contracts/provider_execution.py",
    ROOT / "backend/src/ragkb/adapters/provider_http.py",
    ROOT / "backend/src/ragkb/infrastructure/uat_artifacts.py",
    ROOT / "backend/tests/test_uat_generic_remediation.py",
    ROOT / "backend/tests/test_uat_future_claim_runner.py",
    ROOT / "backend/tests/test_uat_error_case_retest.py",
    ROOT / "scripts/plan_uat_future_claim_remediation.py",
    ROOT / "scripts/prepare_uat_future_error_retest.py",
    ROOT / "scripts/run_uat_future_error_retest.py",
    ROOT / "scripts/check_uat_generic_remediation.py",
    ROOT / "scripts/run_quality.py",
)


def _historical_literals() -> tuple[set[str], set[str], set[str]]:
    candidates: set[str] = set()
    answers: set[str] = set()
    references: set[str] = set()
    for line in REVIEW.read_text(encoding="utf-8").splitlines():
        loaded = json.loads(line)
        if not isinstance(loaded, dict):
            raise RuntimeError("UAT_GENERIC_REMEDIATION_REVIEW_ROW_INVALID")
        candidate = loaded.get("candidate_id")
        answer = loaded.get("answer")
        reference = loaded.get("corrective_reference")
        if isinstance(candidate, str) and candidate:
            candidates.add(candidate)
        if isinstance(answer, str) and answer:
            answers.add(answer)
        if isinstance(reference, str) and reference:
            references.add(reference)
    return candidates, answers, references


def main() -> int:
    if not REVIEW.is_file():
        raise RuntimeError("UAT_GENERIC_REMEDIATION_REVIEW_PACKAGE_MISSING")
    candidates, answers, references = _historical_literals()
    source_text = {path: path.read_text(encoding="utf-8") for path in SOURCES}
    combined = "\n".join(source_text.values())
    historical_matches = sorted(
        value
        for value in candidates | answers | references
        if len(value) >= 4 and value in combined
    )
    identifier_literals = re.findall(r"\b[0-9a-f]{20}\b", combined)
    if historical_matches or identifier_literals:
        raise RuntimeError("UAT_GENERIC_REMEDIATION_CONTENT_DIRECTED_LITERAL_FOUND")
    print(
        json.dumps(
            {
                "review_candidate_count": len(candidates),
                "review_answer_count": len(answers),
                "review_corrective_reference_count": len(references),
                "scanned_source_count": len(SOURCES),
                "historical_literal_match_count": 0,
                "candidate_identifier_literal_count": 0,
                "provider_call_count": 0,
                "network_call_count": 0,
                "content_output": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
