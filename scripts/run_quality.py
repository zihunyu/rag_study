"""Run every locally available code-quality check for the current stage."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = False


def _run(name: str, command: list[str], working_directory: Path = ROOT) -> dict[str, object]:
    if not RUN_INTEGRATION and name == "final_local_samples":
        print(f"===== {name} =====\nSKIPPED: requires immutable real-provider artifacts")
        return {"name": name, "status": "SKIPPED"}
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=working_directory,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(f"===== {name} =====")
    console_encoding = sys.stdout.encoding or "utf-8"
    safe_output = completed.stdout.encode(console_encoding, errors="replace").decode(
        console_encoding
    )
    print(safe_output, end="")
    return {"name": name, "status": "PASSED" if completed.returncode == 0 else "FAILED"}


def main() -> int:
    global RUN_INTEGRATION
    parser = argparse.ArgumentParser()
    parser.add_argument("--integration", action="store_true")
    args = parser.parse_args()
    RUN_INTEGRATION = args.integration
    checks: list[dict[str, object]] = []
    if sys.version_info[:2] != (3, 12):
        checks.append({"name": "python_version", "status": "FAILED"})
    else:
        checks.append({"name": "python_version", "status": "PASSED"})
    checks.extend(
        [
            _run("bootstrap", [sys.executable, "scripts/bootstrap.py", "--check"]),
            _run(
                "config",
                [sys.executable, "scripts/check_env.py", "--gate", "G4", "--allow-blocked"],
            ),
            _run("ruff", [sys.executable, "-m", "ruff", "check", "."]),
            _run("ruff_format", [sys.executable, "-m", "ruff", "format", "--check", "."]),
            _run(
                "pytest_coverage",
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "--cov=ragkb",
                    "--cov-branch",
                    "--cov-report=term-missing:skip-covered",
                    "--cov-fail-under=70",
                ],
            ),
            _run(
                "spikes",
                [
                    sys.executable,
                    "scripts/run_spikes.py",
                    "--all",
                    "--output-dir",
                    "artifacts/g4/spikes",
                ],
            ),
            _run(
                "zilliz_plan",
                [
                    sys.executable,
                    "scripts/plan_zilliz_collection.py",
                    "--output",
                    "artifacts/g2/zilliz-create-plan.json",
                ],
            ),
            _run(
                "model_probe_plan",
                [
                    sys.executable,
                    "scripts/plan_model_probes.py",
                    "--output",
                    "artifacts/g2/model-probe-plan.json",
                ],
            ),
            _run("g3_eval", [sys.executable, "scripts/check_g3_eval.py"]),
            _run("rag_quality", [sys.executable, "scripts/check_rag_quality.py"]),
            _run(
                "g4_format_inputs",
                [
                    sys.executable,
                    "scripts/check_format_samples.py",
                    "--allow-blocked",
                    "--output",
                    "artifacts/g4/format-inputs.json",
                ],
            ),
            _run(
                "g4_local_validation",
                [
                    sys.executable,
                    "scripts/check_g4_validation.py",
                    "--output",
                    "artifacts/g4/local-validation.json",
                ],
            ),
            _run(
                "operations_plan",
                [
                    sys.executable,
                    "scripts/plan_operations.py",
                    "--output",
                    "artifacts/implementation/operations-plan.json",
                ],
            ),
            _run("local_stack_plan", [sys.executable, "scripts/local_stack.py", "plan"]),
            _run(
                "offline_assurance",
                [
                    sys.executable,
                    "scripts/generate_assurance.py",
                    "--output",
                    "artifacts/implementation/assurance.json",
                ],
            ),
            _run(
                "final_validation_plan",
                [
                    sys.executable,
                    "scripts/generate_final_validation_plan.py",
                    "--output",
                    "artifacts/implementation/final-validation-plan.json",
                ],
            ),
            _run(
                "final_local_samples",
                [
                    sys.executable,
                    "scripts/validate_local_samples.py",
                    "--details",
                    "artifacts/final-validation/local-samples/details.json",
                    "--external-plan",
                    "artifacts/final-validation/external-call-plan.json",
                ],
            ),
            _run(
                "claim_content_scan",
                [sys.executable, "scripts/check_uat_generic_remediation.py"],
            ),
            _run("backend_entry", [sys.executable, "run_backend.py", "--check"]),
            _run("worker_entry", [sys.executable, "run_worker.py", "--check"]),
            _run("mineru_entry", [sys.executable, "run_mineru.py", "--check"]),
            _run("migration_entry", [sys.executable, "scripts/run_migrations.py", "--check"]),
            _run("openapi_snapshot", [sys.executable, "scripts/export_openapi.py", "--check"]),
            _run("secret_scan", [sys.executable, "scripts/scan_secrets.py"]),
            _run("frontend", [shutil.which("npm") or "npm", "run", "check"], ROOT / "frontend"),
            _run("mypy", [sys.executable, "-m", "mypy", "backend/src/ragkb"]),
        ]
    )
    summary = {
        "checks": checks,
        "failed": [item["name"] for item in checks if item["status"] == "FAILED"],
        "skipped": [item["name"] for item in checks if item["status"] == "SKIPPED"],
    }
    output = ROOT / "artifacts/implementation/quality-summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
