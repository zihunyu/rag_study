"""Run static secret scanning without reading config/.env or printing values."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.engineering_security.secret_scan import scan_repository_for_secrets  # noqa: E402


def main() -> int:
    findings = scan_repository_for_secrets(ROOT)
    print(
        json.dumps(
            {
                "finding_count": len(findings),
                "findings": findings,
                "config_env_scanned": False,
                "secret_values_in_output": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
