"""Start the worker through a direct native Python entry point."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.runtime import run_worker  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_worker())
