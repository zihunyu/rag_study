"""Redis JSON cache and atomic fixed-window rate-limit adapter."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ragkb.config import EnvSettings

RATE_LIMIT_SCRIPT = """
local value = redis.call('INCR', KEYS[1])
if value == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return value
""".strip()


def _redis_client(**kwargs: Any) -> Any:
    import redis

    return redis.Redis(**kwargs)


class RedisCacheRateLimitAdapter:
    revision = "redis-cache-rate-limit:g2-v1"

    def __init__(
        self,
        settings: EnvSettings,
        *,
        client_factory: Callable[..., Any] = _redis_client,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._client: Any | None = None

    def connect(self) -> Any:
        password = self._settings.redis_password
        self._client = self._client_factory(
            host=self._settings.redis_host,
            port=self._settings.redis_port,
            username=self._settings.redis_user or None,
            password=password.get_secret_value() if password is not None else None,
            db=self._settings.redis_db,
            ssl=self._settings.redis_ssl,
            socket_timeout=self._settings.redis_timeout_seconds,
            decode_responses=True,
        )
        return self._client

    def _connected(self) -> Any:
        return self._client if self._client is not None else self.connect()

    def _key(self, namespace: str, key: str) -> str:
        if not namespace or not key or any(character.isspace() for character in namespace):
            raise ValueError("cache namespace and key must be non-empty")
        return f"{self._settings.redis_key_prefix}{namespace}:{key}"

    def get_json(self, namespace: str, key: str) -> dict[str, Any] | None:
        raw = self._connected().get(self._key(namespace, key))
        if raw is None:
            return None
        loaded = json.loads(str(raw))
        if not isinstance(loaded, dict):
            raise ValueError("cached JSON value must be an object")
        return loaded

    def set_json(self, namespace: str, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        if ttl_seconds < 1:
            raise ValueError("cache TTL must be positive")
        self._connected().set(
            self._key(namespace, key),
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ex=ttl_seconds,
        )

    def allow(self, bucket: str, subject: str, *, limit: int, window_seconds: int) -> bool:
        if limit < 1 or window_seconds < 1:
            raise ValueError("rate limit and window must be positive")
        count = int(
            self._connected().eval(
                RATE_LIMIT_SCRIPT,
                1,
                self._key("rate", f"{bucket}:{subject}"),
                window_seconds,
            )
        )
        return count <= limit

    def safe_status(self) -> dict[str, object]:
        return {
            "adapter": self.revision,
            "host_configured": bool(self._settings.redis_host),
            "ssl_enabled": self._settings.redis_ssl,
            "password_in_status": False,
            "real_connection_attempted": False,
        }
