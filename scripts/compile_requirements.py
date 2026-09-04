"""Regenerate runtime, development, and lock-tool requirements deterministically."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIP_TOOLS_VERSION = "7.6.1"


def _compile(output: str, source: str, *extras: str) -> None:
    command = [
        sys.executable,
        "-m",
        "piptools",
        "compile",
        "--quiet",
        "--generate-hashes",
        "--allow-unsafe",
        "--reuse-hashes",
        "--resolver=backtracking",
        "--strip-extras",
        f"--output-file={output}",
        *extras,
        source,
    ]
    subprocess.run(command, cwd=ROOT, check=True)  # noqa: S603


def main() -> int:
    actual = importlib.metadata.version("pip-tools")
    if actual != PIP_TOOLS_VERSION:
        raise RuntimeError(
            f"pip-tools {PIP_TOOLS_VERSION} is required; found {actual}. "
            "Install requirements-tools.lock first."
        )
    _compile("requirements-tools.lock", "requirements-tools.in")
    _compile("requirements.lock", "pyproject.toml", "--extra=observability")
    _compile(
        "requirements-dev.lock",
        "pyproject.toml",
        "--extra=dev",
        "--extra=observability",
        "--constraint=requirements.lock",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
