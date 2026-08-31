"""Inspect or start the self-hosted-first MinerU native wrapper."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend/src"))
from ragkb.mineru_runtime import run_mineru  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_mineru())
