"""Account-wide rolling request/token reservations shared by API and workers via Redis."""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import redis

from ragkb.application.cancellation import check_cancelled
from ragkb.application.deadlines import remaining_timeout
from ragkb.config import EnvSettings
from ragkb.domain.errors import ProviderRateLimited, ProviderTimeout, ProviderUnavailable

operation: ContextVar[tuple[str, str, str]] = ContextVar("provider_operation", default=("", "", ""))


@contextmanager
def provider_operation(version: str, asset: str, role: str) -> Iterator[None]:
    token = operation.set((version, asset, role))
    try:
        yield
    finally:
        operation.reset(token)


_ACQUIRE = """
local now=tonumber(ARGV[1]); local reservation=tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now)
local expired=redis.call('ZRANGEBYSCORE',KEYS[2],'-inf',now-60)
for _,id in ipairs(expired) do redis.call('HDEL',KEYS[3],id) end
redis.call('ZREMRANGEBYSCORE',KEYS[2],'-inf',now-60)
local tokens=0; for _,v in ipairs(redis.call('HVALS',KEYS[3])) do tokens=tokens+tonumber(v) end
local cool=tonumber(redis.call('GET',KEYS[4]) or '0')
if cool>now or redis.call('ZCARD',KEYS[1])>=tonumber(ARGV[5]) or
redis.call('ZCARD',KEYS[2])>=tonumber(ARGV[6]) or
tokens+reservation>tonumber(ARGV[7]) then return 0 end
redis.call('ZADD',KEYS[1],now+tonumber(ARGV[3]),ARGV[2])
redis.call('ZADD',KEYS[2],now,ARGV[2]); redis.call('HSET',KEYS[3],ARGV[2],reservation)
redis.call('EXPIRE',KEYS[1],3600)
redis.call('EXPIRE',KEYS[2],120); redis.call('EXPIRE',KEYS[3],120)
return 1
"""


def estimate_tokens(value: Any) -> int:
    if isinstance(value, dict):
        if value.get("type") == "image_url":
            return 3500  # bounded 1600-pixel views; reserve conservatively, settle actual usage
        return sum(
            estimate_tokens(v)
            for key, v in value.items()
            if key not in {"model", "response_format"}
        )
    if isinstance(value, list):
        return sum(estimate_tokens(v) for v in value)
    return len(value) if isinstance(value, str) else 0


class AccountLimiter:
    def __init__(self, settings: EnvSettings) -> None:
        self.settings = settings
        self.redis = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            username=settings.redis_user or None,
            password=settings.redis_password.get_secret_value()
            if settings.redis_password
            else None,
            ssl=settings.redis_ssl,
            socket_timeout=settings.redis_timeout_seconds,
            decode_responses=True,
        )

    @contextmanager
    def reserve(
        self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float
    ) -> Iterator[tuple[list[str], str]]:
        material = self.settings.model_account_group or (
            str(urlsplit(url).hostname) + "\n" + headers.get("Authorization", "")
        )
        digest = hashlib.sha256(material.encode()).hexdigest()[:32]
        prefix = self.settings.redis_key_prefix + "account:{" + digest + "}:"
        keys = [prefix + value for value in ("active", "requests", "tokens", "cooldown")]
        identity = uuid.uuid4().hex
        reserved = estimate_tokens(payload) + int(payload.get("max_tokens", 0))
        if reserved > self.settings.model_account_tokens_per_minute:
            raise ProviderRateLimited("MODEL_ACCOUNT_REQUEST_EXCEEDS_TOKEN_BUDGET")
        deadline = time.monotonic() + remaining_timeout(timeout)
        acquired = False
        try:
            while time.monotonic() < deadline:
                check_cancelled()
                acquired = bool(
                    self.redis.eval(
                        _ACQUIRE,
                        len(keys),
                        *keys,
                        str(time.time()),
                        identity,
                        str(max(1, deadline - time.monotonic())),
                        str(reserved),
                        str(self.settings.model_account_max_concurrency),
                        str(self.settings.model_account_requests_per_minute),
                        str(self.settings.model_account_tokens_per_minute),
                    )
                )
                if acquired:
                    break
                time.sleep(0.1)
            if not acquired:
                raise ProviderTimeout("MODEL_ACCOUNT_QUOTA_WAIT_TIMEOUT")
            yield keys, identity
        except redis.RedisError as error:
            raise ProviderUnavailable("MODEL_ACCOUNT_COORDINATOR_UNAVAILABLE") from error
        finally:
            if acquired:
                try:
                    self.redis.zrem(keys[0], identity)
                except redis.RedisError:
                    pass  # reservation has a hard deadline; never release someone else's lease

    def settle(
        self,
        lease: tuple[list[str], str],
        usage: Mapping[str, Any],
        status: int,
        retry_after: str = "",
    ) -> None:
        keys, identity = lease
        total = usage.get("total_tokens")
        if isinstance(total, int) and total >= 0:
            self.redis.eval(
                "if redis.call('HEXISTS',KEYS[1],ARGV[1])==1 then "
                "return redis.call('HSET',KEYS[1],ARGV[1],ARGV[2]) end return 0",
                1,
                keys[2],
                identity,
                str(total),
            )
        if status == 429:
            try:
                seconds = min(300, max(1, float(retry_after)))
            except ValueError:
                try:
                    seconds = min(
                        300, max(1, parsedate_to_datetime(retry_after).timestamp() - time.time())
                    )
                except (ValueError, TypeError, OverflowError):
                    seconds = 5
            # Concurrent responses must never shorten an existing account cooldown.
            self.redis.eval(
                "if tonumber(redis.call('GET',KEYS[1]) or '0') < tonumber(ARGV[1]) then "
                "return redis.call('SET',KEYS[1],ARGV[1],'EX',ARGV[2]) end return 0",
                1,
                keys[3],
                str(time.time() + seconds),
                str(max(1, int(seconds) + 1)),
            )
