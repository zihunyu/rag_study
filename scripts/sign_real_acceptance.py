"""Sign a passed real-provider quality report for production acceptance."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.config import load_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--quality-report", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--embedding-revision", required=True)
    parser.add_argument("--reranker-revision", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--prompt-revision", required=True)
    parser.add_argument("--index-generation-id", required=True)
    parser.add_argument("--dataset-revision", required=True)
    parser.add_argument("--ci-run-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.approved:
        raise SystemExit("REAL_ACCEPTANCE_SIGNING_APPROVAL_REQUIRED")
    settings = load_env(ROOT).settings
    if settings is None or settings.rag_acceptance_signing_key is None:
        raise SystemExit("RAG_ACCEPTANCE_SIGNING_KEY_REQUIRED")
    report_path = args.quality_report.resolve()
    report: Any = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("passed") is not True:
        raise SystemExit("QUALITY_REPORT_NOT_PASSED")
    metrics = report.get("metrics")
    thresholds = report.get("thresholds")
    if not isinstance(metrics, dict) or not isinstance(thresholds, dict):
        raise SystemExit("QUALITY_REPORT_METRICS_INVALID")
    case_count = int(report.get("case_count", 0))
    buckets = report.get("query_type_buckets")
    query_types = tuple(sorted(map(str, buckets))) if isinstance(buckets, dict) else ()
    if case_count < settings.rag_acceptance_min_cases:
        raise SystemExit("REAL_ACCEPTANCE_CASE_COUNT_BELOW_MINIMUM")
    if not set(settings.rag_acceptance_required_query_types).issubset(query_types):
        raise SystemExit("REAL_ACCEPTANCE_QUERY_TYPE_COVERAGE_INCOMPLETE")
    report_provider = str(report.get("provider", ""))
    if args.provider != report_provider or any(
        marker in report_provider.casefold() for marker in ("local", "deterministic", "synthetic")
    ):
        raise SystemExit("REAL_ACCEPTANCE_PROVIDER_INVALID")
    if args.dataset_revision != str(report.get("dataset_revision", "")):
        raise SystemExit("REAL_ACCEPTANCE_DATASET_REVISION_MISMATCH")
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
    body = {
        "provider": args.provider,
        "embedding_revision": args.embedding_revision,
        "reranker_revision": args.reranker_revision,
        "model_revision": args.model_revision,
        "prompt_revision": args.prompt_revision,
        "index_generation_id": args.index_generation_id,
        "dataset_revision": args.dataset_revision,
        "case_count": case_count,
        "query_types": list(query_types),
        "metrics": metrics,
        "thresholds": thresholds,
        "passed": True,
        "evaluated_at_epoch": int(time.time()),
        "quality_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "source_commit": commit,
        "ci_run_id": args.ci_run_id,
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
