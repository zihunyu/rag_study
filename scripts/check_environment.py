"""Report G0 tool/runtime availability without inspecting secret values."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Check native G0 development prerequisites")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    target_python = (3, 12)
    report = {
        "report_schema_version": 1,
        "python": {
            "current": ".".join(str(item) for item in sys.version_info[:3]),
            "target": "3.12",
            "target_satisfied": sys.version_info[:2] == target_python,
        },
        "python_modules": {
            name: importlib.util.find_spec(name) is not None
            for name in ("jsonschema", "mypy", "pytest", "pydantic", "ruff", "yaml")
        },
        "native_commands": {
            name: shutil.which(name) is not None
            for name in ("mysql", "redis-server", "node", "npm", "ruff")
        },
        "zilliz_cloud": "not contacted; validate ZILLIZ_CLOUD_* with scripts/check_env.py",
        "secrets_inspected": False,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
