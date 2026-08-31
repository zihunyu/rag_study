"""Create the project-local Python 3.12 environment and install development tools."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"


def _venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _venv_is_python_312(venv_python: Path) -> bool:
    if not venv_python.is_file():
        return False
    completed = subprocess.run(  # noqa: S603
        [
            str(venv_python),
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)",
        ],
        cwd=ROOT,
        check=False,
    )
    return completed.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap the project Python 3.12 environment")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        print("Bootstrap requires Python 3.12; invoke this script with the workspace Python 3.12.")
        return 2
    venv_python = _venv_python()
    if args.check:
        if not _venv_is_python_312(venv_python):
            print("bootstrap_ready=false reason=venv_missing_or_not_python_3_12")
            return 2
        completed = subprocess.run(  # noqa: S603
            [str(venv_python), "--version"], cwd=ROOT, check=False
        )
        return completed.returncode
    if venv_python.is_file() and not _venv_is_python_312(venv_python):
        print("Existing .venv is not Python 3.12; remove it explicitly before bootstrap.")
        return 2
    if not venv_python.is_file():
        completed = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "venv", str(VENV)], cwd=ROOT, check=False
        )
        if completed.returncode:
            return completed.returncode
    commands = (
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
        [str(venv_python), "-m", "pip", "install", "-e", ".[dev]"],
    )
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, check=False)  # noqa: S603
        if completed.returncode:
            return completed.returncode
    print("bootstrap_ready=true python=3.12 environment=.venv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
