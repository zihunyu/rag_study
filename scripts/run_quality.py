"""Run every locally available G0 quality check through native processes."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(name: str, command: list[str], working_directory: Path = ROOT) -> dict[str, object]:
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=working_directory,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(f"===== {name} =====")
    print(completed.stdout, end="")
    return {"name": name, "status": "PASSED" if completed.returncode == 0 else "FAILED"}


def main() -> int:
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
                [sys.executable, "scripts/check_config.py", "--gate", "G0", "--allow-blocked"],
            ),
            _run("ruff", [sys.executable, "-m", "ruff", "check", "."]),
            _run("ruff_format", [sys.executable, "-m", "ruff", "format", "--check", "."]),
            _run("pytest", [sys.executable, "-m", "pytest"]),
            _run(
                "spikes",
                [
                    sys.executable,
                    "scripts/run_g0_spikes.py",
                    "--all",
                    "--output-dir",
                    "artifacts/g0/spikes",
                ],
            ),
            _run("backend_entry", [sys.executable, "run_backend.py", "--check"]),
            _run("worker_entry", [sys.executable, "run_worker.py", "--once"]),
            _run("mineru_entry", [sys.executable, "run_mineru.py", "--check"]),
            _run("migration_entry", [sys.executable, "scripts/run_migrations.py", "--check"]),
            _run("frontend", [shutil.which("npm") or "npm", "run", "check"], ROOT / "frontend"),
            _run("mypy", [sys.executable, "-m", "mypy", "backend/src/ragkb"]),
        ]
    )
    summary = {
        "checks": checks,
        "failed": [item["name"] for item in checks if item["status"] == "FAILED"],
        "skipped": [],
    }
    output = ROOT / "artifacts/g0/quality-summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
