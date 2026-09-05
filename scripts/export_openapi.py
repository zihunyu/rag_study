"""Export the frozen G1 OpenAPI document from the native app factory."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.api.app import create_app  # noqa: E402
from ragkb.runtime_components import build_runtime_components  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or verify the G1 OpenAPI snapshot")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = ROOT / "docs/openapi/openapi-v1.json"
    # Schema export must never initialize the configured business database.
    os.environ.update(
        APP_ENV="testing",
        RAG_RUNTIME_PROFILE="local",
        VECTOR_BACKEND="local",
        AUTH_MODE="local_single_user",
        REAL_PROVIDER_CALLS_ENABLED="false",
        EXTERNAL_LIFECYCLE_MUTATIONS_ENABLED="false",
        OTEL_ENABLED="false",
    )
    with TemporaryDirectory(prefix="rag-openapi-") as folder:
        runtime = build_runtime_components(
            storage_root=Path(folder) / "storage", database_path=Path(folder) / "schema.sqlite3"
        )
        rendered = (
            json.dumps(create_app(runtime).openapi(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
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
