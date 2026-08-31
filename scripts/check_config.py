"""User-facing direct Python entry point declared by project-inputs.yaml."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.config.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
