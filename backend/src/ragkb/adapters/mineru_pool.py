"""Secret-safe MinerU token pool with round-robin failover and cooldown."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

from pydantic import SecretStr


class MinerURetryableError(RuntimeError):
    """Safe retryable error that never contains credentials."""

    def __init__(self, code: str, retry_after_seconds: float) -> None:
        super().__init__(f"{code}; retry_after_seconds={max(0, retry_after_seconds):.3f}")
        self.code = code
        self.retry_after_seconds = max(0, retry_after_seconds)


class MinerURateLimitError(RuntimeError):
    pass


@dataclass
class _TokenState:
    token: SecretStr = field(repr=False)
    in_flight: int = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0


class MinerUTokenLease(AbstractContextManager["MinerUTokenLease"]):
    def __init__(
        self, release_callback: Callable[[int], None], slot: int, token: SecretStr
    ) -> None:
        self._release_callback = release_callback
        self.slot = slot
        self._token = token
        self._released = False

    def secret_value(self) -> str:
        return self._token.get_secret_value()

    def release(self) -> None:
        if not self._released:
            self._release_callback(self.slot)
            self._released = True

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    def __repr__(self) -> str:
        return f"MinerUTokenLease(slot={self.slot}, token=**********)"


class MinerUTokenPool[ResultT]:
    revision = "mineru-token-pool:v1"

    def __init__(
        self,
        tokens: Sequence[SecretStr | str],
        *,
        max_concurrency_per_token: int,
        max_failures: int,
        cooldown_seconds: float,
        failover_enabled: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not tokens:
            raise ValueError("MINERU_TOKENS has no configured token")
        if max_concurrency_per_token < 1 or max_failures < 1 or cooldown_seconds <= 0:
            raise ValueError("MinerU token limits must be positive")
        self._states = [
            _TokenState(token if isinstance(token, SecretStr) else SecretStr(token))
            for token in tokens
        ]
        self._max_concurrency = max_concurrency_per_token
        self._max_failures = max_failures
        self._cooldown_seconds = cooldown_seconds
        self._failover_enabled = failover_enabled
        self._clock = clock
        self._cursor = 0
        self._lock = threading.Lock()

    def _acquire(
        self, *, exclude: set[int] | None = None, now: float | None = None
    ) -> MinerUTokenLease:
        timestamp = self._clock() if now is None else now
        excluded = exclude or set()
        with self._lock:
            for offset in range(len(self._states)):
                slot = (self._cursor + offset) % len(self._states)
                state = self._states[slot]
                if (
                    slot in excluded
                    or state.cooldown_until > timestamp
                    or state.in_flight >= self._max_concurrency
                ):
                    continue
                state.in_flight += 1
                self._cursor = (slot + 1) % len(self._states)
                return MinerUTokenLease(self._release, slot, state.token)
            retry_after = min(
                (
                    max(0, state.cooldown_until - timestamp)
                    for slot, state in enumerate(self._states)
                    if slot not in excluded and state.cooldown_until > timestamp
                ),
                default=0,
            )
        raise MinerURetryableError("MINERU_TOKENS_UNAVAILABLE", retry_after)

    def acquire(self, *, now: float | None = None) -> MinerUTokenLease:
        return self._acquire(now=now)

    def acquire_slot(self, slot: int, *, now: float | None = None) -> MinerUTokenLease:
        timestamp = self._clock() if now is None else now
        with self._lock:
            if slot < 0 or slot >= len(self._states):
                raise MinerURetryableError("MINERU_TOKEN_SLOT_INVALID", 0)
            state = self._states[slot]
            if state.cooldown_until > timestamp or state.in_flight >= self._max_concurrency:
                raise MinerURetryableError(
                    "MINERU_TOKEN_SLOT_UNAVAILABLE",
                    max(0, state.cooldown_until - timestamp),
                )
            state.in_flight += 1
            return MinerUTokenLease(self._release, slot, state.token)

    def _release(self, slot: int) -> None:
        with self._lock:
            state = self._states[slot]
            state.in_flight = max(0, state.in_flight - 1)

    def _success(self, slot: int) -> None:
        with self._lock:
            state = self._states[slot]
            state.consecutive_failures = 0

    def record_success(self, slot: int) -> None:
        self._success(slot)

    def _failure(self, slot: int, *, rate_limited: bool, now: float) -> None:
        with self._lock:
            state = self._states[slot]
            state.consecutive_failures += 1
            if rate_limited or state.consecutive_failures >= self._max_failures:
                state.cooldown_until = now + self._cooldown_seconds

    def record_failure(
        self, slot: int, *, rate_limited: bool = False, now: float | None = None
    ) -> None:
        self._failure(
            slot,
            rate_limited=rate_limited,
            now=self._clock() if now is None else now,
        )

    def call(
        self,
        operation: Callable[[str], ResultT],
        *,
        now: float | None = None,
    ) -> ResultT:
        timestamp = self._clock() if now is None else now
        attempted: set[int] = set()
        last_code = "MINERU_REQUEST_FAILED"
        while len(attempted) < len(self._states):
            lease = self._acquire(exclude=attempted, now=timestamp)
            attempted.add(lease.slot)
            try:
                result = operation(lease.secret_value())
            except MinerURateLimitError:
                last_code = "MINERU_RATE_LIMITED"
                self._failure(lease.slot, rate_limited=True, now=timestamp)
                if not self._failover_enabled:
                    raise MinerURetryableError(last_code, self._cooldown_seconds) from None
            except Exception:
                self._failure(lease.slot, rate_limited=False, now=timestamp)
                if not self._failover_enabled:
                    raise MinerURetryableError(last_code, self._cooldown_seconds) from None
            else:
                self._success(lease.slot)
                return result
            finally:
                lease.release()
        retry_after = min(
            (
                max(0, state.cooldown_until - timestamp)
                for state in self._states
                if state.cooldown_until > timestamp
            ),
            default=self._cooldown_seconds,
        )
        raise MinerURetryableError(last_code, retry_after)

    def status(self, *, now: float | None = None) -> dict[str, object]:
        timestamp = self._clock() if now is None else now
        with self._lock:
            return {
                "revision": self.revision,
                "strategy": "round_robin",
                "token_count": len(self._states),
                "available_count": sum(
                    1
                    for state in self._states
                    if state.cooldown_until <= timestamp and state.in_flight < self._max_concurrency
                ),
                "slots": [
                    {
                        "slot": slot,
                        "in_flight": state.in_flight,
                        "consecutive_failures": state.consecutive_failures,
                        "cooling_down": state.cooldown_until > timestamp,
                    }
                    for slot, state in enumerate(self._states)
                ],
                "secret_values_in_status": False,
            }
