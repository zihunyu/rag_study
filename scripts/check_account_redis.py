"""Real Redis login throttling against an isolated API/database and unique key prefix."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
prefix = "ragkb:account-acceptance:" + uuid.uuid4().hex + ":"
os.environ.update(
    APP_ENV="testing",
    RAG_RUNTIME_PROFILE="local",
    VECTOR_BACKEND="local",
    AUTH_MODE="password",
    REDIS_KEY_PREFIX=prefix,
    REAL_PROVIDER_CALLS_ENABLED="false",
    OCR_ENABLED="false",
    MODEL_ACCOUNT_LIMIT_ENABLED="false",
    MODEL_USAGE_ENABLED="false",
    EXTERNAL_LIFECYCLE_MUTATIONS_ENABLED="false",
    OTEL_ENABLED="false",
)

from fastapi.testclient import TestClient  # noqa: E402
from ragkb.adapters.redis_cache import RedisCacheRateLimitAdapter  # noqa: E402
from ragkb.api.app import create_app  # noqa: E402
from ragkb.runtime_components import build_runtime_components  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="ragkb-account-redis-") as folder:
        runtime = build_runtime_components(
            storage_root=Path(folder) / "storage", database_path=Path(folder) / "database.sqlite3"
        )
        runtime.accounts.bootstrap("admin", "Redis fixture password only!")
        limiter = RedisCacheRateLimitAdapter(runtime.settings)
        redis = limiter.connect()
        try:
            app = create_app(
                replace(
                    runtime, settings=runtime.settings.model_copy(update={"app_env": "development"})
                )
            )
            with TestClient(app) as client:
                token = client.get("/api/auth/csrf").json()["csrf_token"]
                client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": token})
                codes = [
                    client.post(
                        "/api/auth/login", json={"username": "admin", "password": "wrong"}
                    ).status_code
                    for _ in range(11)
                ]
                assert codes == [401] * 10 + [429], codes
                responses = [
                    client.post(
                        "/api/auth/login",
                        json={"username": "unassigned" + str(i), "password": "wrong"},
                    )
                    for i in range(30)
                ]
                assert [r.status_code for r in responses] == [401] * 29 + [429]
                assert responses[-1].headers["retry-after"] == "900"
        finally:
            keys = list(redis.scan_iter(match=prefix + "*"))
            assert all(str(k).startswith(prefix) for k in keys)
            if keys:
                redis.delete(*keys)
            redis.close()
    result = {
        "account_limit": "passed",
        "address_limit": "passed",
        "retry_after": 900,
        "isolated_database": True,
        "unique_test_keys_removed": True,
    }
    (ROOT / "artifacts/reviews/20260908-account-access/redis-acceptance.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
