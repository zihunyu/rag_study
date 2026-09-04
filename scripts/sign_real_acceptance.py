"""Sign a passed real-provider quality report for production acceptance."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.config import load_env  # noqa: E402
from ragkb.evaluation.quality_thresholds import (  # noqa: E402
    QualityThresholdPolicy,
    load_quality_threshold_policy,
)


def validate_signable_report(
    report: dict[str, Any],
    policy: QualityThresholdPolicy,
    *,
    min_cases: int,
    required_query_types: tuple[str, ...],
    source_commit: str,
) -> dict[str, Any]:
    required_true = (
        "passed",
        "real_acceptance",
        "quality_passed",
        "expected_answers_passed",
        "cases_passed",
        "prompt_injection_passed",
        "cost_calculated",
    )
    if any(report.get(field) is not True for field in required_true):
        raise ValueError("QUALITY_REPORT_REQUIRED_GATE_NOT_PASSED")
    metrics = report.get("metrics")
    thresholds = report.get("thresholds")
    if not isinstance(metrics, dict) or not isinstance(thresholds, dict):
        raise ValueError("QUALITY_REPORT_METRICS_INVALID")
    normalized_thresholds = {str(key): float(value) for key, value in thresholds.items()}
    if (
        normalized_thresholds != dict(policy.values)
        or report.get("threshold_sha256") != policy.sha256
        or report.get("threshold_revision") != policy.revision
        or any(
            name not in metrics or float(metrics[name]) < minimum
            for name, minimum in policy.values.items()
        )
    ):
        raise ValueError("QUALITY_REPORT_THRESHOLD_POLICY_MISMATCH")
    case_count = int(report.get("case_count", 0))
    answer_results = report.get("answer_case_results")
    if (
        case_count < min_cases
        or not isinstance(answer_results, list)
        or len(answer_results) != case_count
        or any(
            not isinstance(item, dict) or item.get("passed") is not True for item in answer_results
        )
    ):
        raise ValueError("REAL_ACCEPTANCE_CASE_RESULTS_INVALID")
    raw_query_types = report.get("query_types")
    query_types = (
        tuple(sorted(map(str, raw_query_types))) if isinstance(raw_query_types, list) else ()
    )
    if not set(required_query_types).issubset(query_types):
        raise ValueError("REAL_ACCEPTANCE_QUERY_TYPE_COVERAGE_INCOMPLETE")
    tested = report.get("tested_generations")
    if (
        not isinstance(tested, dict)
        or set(tested) != {"1", "5", "20"}
        or len(set(map(str, tested.values()))) != 3
    ):
        raise ValueError("REAL_ACCEPTANCE_TESTED_GENERATIONS_INVALID")
    if any(
        str(item.get("performance_scale")) not in tested
        or str(item.get("index_generation_id")) != str(tested[str(item.get("performance_scale"))])
        for item in answer_results
    ):
        raise ValueError("REAL_ACCEPTANCE_CASE_GENERATION_MISMATCH")
    index_generation_id = str(report.get("index_generation_id", ""))
    if not index_generation_id or index_generation_id != str(tested["20"]):
        raise ValueError("REAL_ACCEPTANCE_GENERATION_NOT_TESTED")
    performance = report.get("performance")
    budget = report.get("budget")
    if (
        not isinstance(performance, dict)
        or performance.get("performance_scope") != [1, 5, 20]
        or performance.get("slo_claimed") is not False
        or not isinstance(budget, dict)
        or budget.get("automatic_retries") != 0
        or report.get("automatic_retries") != 0
    ):
        raise ValueError("REAL_ACCEPTANCE_SCOPE_OR_RETRY_INVALID")
    limits = budget.get("limits")
    usage = budget.get("usage")
    if not isinstance(limits, dict) or not isinstance(usage, dict):
        raise ValueError("REAL_ACCEPTANCE_BUDGET_INVALID")
    expected_limits = {"provider_calls": 60, "input_tokens": 200_000, "output_tokens": 20_000}
    if limits != expected_limits or any(
        int(usage.get(name, expected_limits[name] + 1)) > maximum
        for name, maximum in expected_limits.items()
    ):
        raise ValueError("REAL_ACCEPTANCE_BUDGET_INVALID")
    cleanup = report.get("cleanup")
    revisions = {
        name: str(report.get(name, ""))
        for name in (
            "provider",
            "embedding_revision",
            "reranker_revision",
            "model_revision",
            "verifier_revision",
            "tokenizer_revision",
            "prompt_revision",
            "dataset_revision",
            "budget_report_sha256",
        )
    }
    if (
        not isinstance(cleanup, dict)
        or cleanup.get("all_removed") is not True
        or any(not value for value in revisions.values())
        or len(revisions["budget_report_sha256"]) != 64
        or any(
            marker in revisions["provider"].casefold()
            for marker in ("local", "deterministic", "synthetic")
        )
        or report.get("source_commit") != source_commit
    ):
        raise ValueError("REAL_ACCEPTANCE_REPORT_BINDING_INVALID")
    return {
        **revisions,
        "index_generation_id": index_generation_id,
        "case_count": case_count,
        "query_types": query_types,
        "metrics": metrics,
        "thresholds": normalized_thresholds,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--quality-report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.approved:
        raise SystemExit("REAL_ACCEPTANCE_SIGNING_APPROVAL_REQUIRED")
    settings = load_env(ROOT).settings
    if settings is None or settings.rag_acceptance_signing_key is None:
        raise SystemExit("RAG_ACCEPTANCE_SIGNING_KEY_REQUIRED")
    report_path = args.quality_report.resolve()
    report: Any = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise SystemExit("QUALITY_REPORT_NOT_PASSED")
    git_executable = shutil.which("git")
    if git_executable is None:
        raise SystemExit("GIT_EXECUTABLE_REQUIRED")
    commit = subprocess.run(  # noqa: S603
        [git_executable, "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        metadata = validate_signable_report(
            report,
            load_quality_threshold_policy(ROOT / "config/rag-quality-thresholds.json"),
            min_cases=settings.rag_acceptance_min_cases,
            required_query_types=settings.rag_acceptance_required_query_types,
            source_commit=commit,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    ci_run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    if not ci_run_id:
        raise SystemExit("GITHUB_RUN_ID_REQUIRED")
    body = {
        "provider": metadata["provider"],
        "embedding_revision": metadata["embedding_revision"],
        "reranker_revision": metadata["reranker_revision"],
        "model_revision": metadata["model_revision"],
        "verifier_revision": metadata["verifier_revision"],
        "tokenizer_revision": metadata["tokenizer_revision"],
        "prompt_revision": metadata["prompt_revision"],
        "index_generation_id": metadata["index_generation_id"],
        "dataset_revision": metadata["dataset_revision"],
        "case_count": metadata["case_count"],
        "query_types": list(metadata["query_types"]),
        "metrics": metadata["metrics"],
        "thresholds": metadata["thresholds"],
        "passed": True,
        "evaluated_at_epoch": int(time.time()),
        "quality_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "source_commit": commit,
        "ci_run_id": ci_run_id,
        "performance_scope": [1, 5, 20],
        "budget_report_sha256": metadata["budget_report_sha256"],
    }
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signed = {
        **body,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "signature": hmac.new(
            settings.rag_acceptance_signing_key.get_secret_value().encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest(),
    }
    output = args.output or settings.rag_acceptance_evidence_path
    output = output if output.is_absolute() else ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(signed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"written": str(output), "secret_in_output": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
