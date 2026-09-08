"""Explicit isolated localhost fixture for real browser authorization acceptance."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
os.environ.update(
    APP_ENV="testing",
    AUTH_MODE="password",
    RAG_RUNTIME_PROFILE="local",
    VECTOR_BACKEND="local",
    REAL_PROVIDER_CALLS_ENABLED="false",
    OCR_ENABLED="false",
    OTEL_ENABLED="false",
    EXTERNAL_LIFECYCLE_MUTATIONS_ENABLED="false",
)

from ragkb.api.app import create_app  # noqa: E402
from ragkb.runtime_components import build_runtime_components  # noqa: E402


def main() -> None:
    import tempfile

    import uvicorn

    output = ROOT / "artifacts/reviews/20260908-account-access"
    output.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="ragkb-account-acceptance-"))
    runtime = build_runtime_components(
        storage_root=folder / "storage", database_path=folder / "database.sqlite3"
    )
    with runtime.accounts.db.transaction() as connection:
        runtime.accounts.db.execute(
            connection,
            "UPDATE knowledge_spaces SET name=? WHERE id=?",
            ("验收 A · 团队手册", runtime.space_id),
        )
    service = runtime.accounts
    assert service is not None
    service.bootstrap("admin", "Fixture-only password 2026!")
    app = create_app(runtime)
    (output / "fixture.json").write_text(
        json.dumps(
            {
                "space_id": runtime.space_id,
                "database": str(folder / "database.sqlite3"),
                "port": 8002,
            }
        ),
        encoding="utf-8",
    )
    uvicorn.run(app, host="127.0.0.1", port=8002, access_log=False)


if __name__ == "__main__":
    main()
