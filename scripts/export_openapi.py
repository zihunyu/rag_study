"""Export the frozen G1 OpenAPI document from the native app factory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.api.app import create_app  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or verify the G1 OpenAPI snapshot")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = ROOT / "docs/openapi/openapi-v1.json"
    rendered = (
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            print("openapi_snapshot=stale")
            return 2
        print("openapi_snapshot=current version=1.0.0")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
