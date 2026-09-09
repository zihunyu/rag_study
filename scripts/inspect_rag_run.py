"""Read a saved RAG failure; defaults to metadata, with optional private content export."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter  # noqa: E402
from ragkb.adapters.mysql_rag import MySQLRAGRunRepository  # noqa: E402
from ragkb.config import load_env  # noqa: E402
from ragkb.domain.answer_conditions import (  # noqa: E402
    ConditionCheckError,
    condition_requirements,
    validate_condition_checks,
)
from ragkb.domain.rag import AskResult, AtomicClaim, DraftAnswer, EvidencePackage  # noqa: E402
from ragkb.infrastructure.rag_repository import _package, _result  # noqa: E402


def summarize_run(package: EvidencePackage, result: AskResult) -> dict[str, Any]:
    diagnostic = package.diagnostics
    failure = diagnostic.get("failure", {})
    detail = failure.get("detail", {})
    return {
        "rag_run_id": package.rag_run_id,
        "status": result.status.value,
        "warnings": list(result.warnings),
        "retrieval_health": package.retrieval_health.value,
        "evidence_count": len(package.evidence),
        "document_count": len({e.document_id for e in package.evidence}),
        "model_revision": package.model_revision,
        "verifier_revision": package.verifier_revision,
        "diagnostics_available": bool(diagnostic),
        "truncated": diagnostic.get("truncated", False),
        "stage": failure.get("stage"),
        "failure_code": failure.get("code"),
        "condition_id": detail.get("condition_id"),
        "evidence_id": detail.get("evidence_id"),
        "failure_reason": detail.get("reason"),
        "verification_stage": detail.get("verification_stage"),
        "batch_number": detail.get("batch_number"),
        "batch_count": detail.get("batch_count"),
        "completed_batches": detail.get("completed_batches"),
        "condition_count": detail.get("condition_count"),
        "model_calls": [
            {key: c.get(key) for key in ("adapter", "model", "elapsed_seconds", "error_type")}
            for c in diagnostic.get("calls", [])
        ],
    }


def replay_conditions(package: EvidencePackage) -> dict[str, Any]:
    """Replay recorded model output locally, without calling a provider or publishing."""
    diagnostic = package.diagnostics
    saved = diagnostic.get("failure", {}).get("draft")
    calls = diagnostic.get("calls", [])
    if not saved or not calls or diagnostic.get("truncated"):
        return {"status": "replay_unavailable"}
    draft = DraftAnswer(
        saved["text"],
        tuple(saved["citation_ids"]),
        tuple(
            AtomicClaim(c["text"], tuple(c["evidence_ids"]), tuple(c.get("visual_fact_ids", ())))
            for c in saved["claims"]
        ),
        synthesized=saved.get("synthesized", False),
    )
    try:
        # Use the exact requirements sent to the failing verifier, not a newly
        # retrieved corpus. Fallback supports older diagnostic records.
        call = next(c for c in reversed(calls) if "claim-verifier" in c.get("adapter", ""))
        sent = json.loads(call["messages"][-1]["content"])
        content = call["response"]["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = (
                content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            )
        loaded = json.loads(content)
        checks = validate_condition_checks(
            loaded.get("condition_checks"),
            sent.get("condition_requirements", condition_requirements(package.evidence)),
            draft,
            package.query,
        )
        return {
            "status": "parsed",
            "conditions": [{"id": c["id"], "status": c["status"]} for c in checks],
        }
    except ConditionCheckError as error:
        return {"status": "reproduced", **error.diagnostic}
    except (KeyError, TypeError, ValueError, StopIteration):
        return {"status": "replay_unavailable"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--include-content",
        action="store_true",
        help="Include private model messages and responses",
    )
    parser.add_argument(
        "--replay-conditions",
        action="store_true",
        help="Recheck saved condition output without model calls",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = load_env(ROOT).settings
    if settings.rag_runtime_profile == "production":
        control = MySQLControlPlaneAdapter(settings)
        try:
            repository = MySQLRAGRunRepository(control)
            package, result = (
                repository.get_package(args.run_id),
                repository.get_result(args.run_id),
            )
        finally:
            control.close()
    else:
        import sqlite3

        path = settings.queue_database_path
        path = path if path.is_absolute() else ROOT / path
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT package_json,result_json FROM rag_runs WHERE run_id=?", (args.run_id,)
            ).fetchone()
        package, result = (
            (_package(json.loads(row[0])), _result(json.loads(row[1]))) if row else (None, None)
        )
    if package is None or result is None:
        print(json.dumps({"status": "run_not_found"}))
        return 2
    report = summarize_run(package, result)
    if args.include_content:
        report["package"] = asdict(package)
    if args.replay_conditions:
        report["replay"] = replay_conditions(package)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(json.dumps({"status": "saved", "output": str(args.output)}, ensure_ascii=False))
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
