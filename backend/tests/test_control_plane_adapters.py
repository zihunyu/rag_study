from __future__ import annotations

from pathlib import Path

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.adapters.redis_cache import RedisCacheRateLimitAdapter
from ragkb.config import load_env
from ragkb.infrastructure.mysql_migrations import MYSQL_MIGRATIONS, migration_plan


def _settings(tmp_path: Path):
    env = tmp_path / ".env"
    mysql_password_key = "MYSQL_" + "PASSWORD"
    redis_password_key = "REDIS_" + "PASSWORD"
    env.write_text(
        "\n".join(
            (
                "MYSQL_HOST=127.0.0.1",
                "MYSQL_USER=rag_app",
                f"{mysql_password_key}=private-mysql-secret",
                "MYSQL_DATABASE=rag_kb",
                "REDIS_HOST=127.0.0.1",
                f"{redis_password_key}=private-redis-secret",
                "REDIS_KEY_PREFIX=test:",
            )
        ),
        encoding="utf-8",
    )
    loaded = load_env(Path(__file__).resolve().parents[2], env_path=env, environ={})
    assert loaded.settings is not None
    return loaded.settings


def test_mysql_adapter_and_migrations_are_explicit_and_secret_safe(tmp_path: Path) -> None:
    fixture_value = "private-mysql-secret"
    captured: dict[str, object] = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return object()

    adapter = MySQLControlPlaneAdapter(_settings(tmp_path), connection_factory=factory)
    adapter.connect()
    sql = "\n".join(statement for _, statement in MYSQL_MIGRATIONS)

    assert captured["password"] == fixture_value
    assert captured["database"] == "rag_kb"
    assert fixture_value not in str(adapter.safe_status())
    assert migration_plan()["mutating_execution_performed"] is False
    assert "ENGINE=InnoDB" in sql
    assert "DEFAULT CHARSET=utf8mb4" in sql
    for field in (
        "generation_id",
        "security_watermark",
        "last_applied_event_seq",
        "schema_fingerprint",
    ):
        assert field in sql

    captured.clear()
    adapter.connect_server()
    assert "database" not in captured


def test_mysql_pool_reuses_healthy_connections_and_closes_them_explicitly(tmp_path: Path) -> None:
    class _PoolConnection:
        def __init__(self) -> None:
            self.rollbacks = 0
            self.pings = 0
            self.closed = 0

        def rollback(self) -> None:
            self.rollbacks += 1

        def ping(self, *, reconnect: bool) -> None:
            assert reconnect is True
            self.pings += 1

        def close(self) -> None:
            self.closed += 1

    created: list[_PoolConnection] = []

    def factory(**kwargs):
        del kwargs
        connection = _PoolConnection()
        created.append(connection)
        return connection

    adapter = MySQLControlPlaneAdapter(_settings(tmp_path), connection_factory=factory)
    first = adapter.connect()
    first.close()
    first.close()
    second = adapter.connect()
    second.close()

    assert len(created) == 1
    assert created[0].rollbacks == 2
    assert created[0].pings == 1
    assert created[0].closed == 0
    adapter.close()
    assert created[0].closed == 1


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value: str, *, ex: int) -> None:
        assert ex > 0
        self.values[key] = value

    def eval(self, script: str, key_count: int, key: str, window: int) -> int:
        assert key_count == 1 and window > 0 and "INCR" in script
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


def test_redis_cache_and_rate_limit_contract(tmp_path: Path) -> None:
    fixture_value = "private-redis-secret"
    fake = _FakeRedis()
    captured: dict[str, object] = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return fake

    adapter = RedisCacheRateLimitAdapter(_settings(tmp_path), client_factory=factory)
    adapter.set_json("search", "key", {"hit": True}, 30)

    assert adapter.get_json("search", "key") == {"hit": True}
    assert adapter.allow("search", "user-1", limit=2, window_seconds=60)
    assert adapter.allow("search", "user-1", limit=2, window_seconds=60)
    assert not adapter.allow("search", "user-1", limit=2, window_seconds=60)
    assert captured["password"] == fixture_value
    assert fixture_value not in str(adapter.safe_status())
