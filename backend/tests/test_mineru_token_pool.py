from __future__ import annotations

import threading

import pytest
from ragkb.adapters.mineru_pool import (
    MinerURateLimitError,
    MinerURetryableError,
    MinerUTokenPool,
)


def _pool(**overrides):
    options = {
        "max_concurrency_per_token": 1,
        "max_failures": 2,
        "cooldown_seconds": 30,
    }
    options.update(overrides)
    return MinerUTokenPool(["secret-a", "secret-b", "secret-c"], **options)


def test_round_robin_lease_order_never_exposes_tokens() -> None:
    pool = _pool()
    slots = []
    for _ in range(4):
        with pool.acquire(now=0) as lease:
            slots.append(lease.slot)
            assert "secret-" not in repr(lease)

    assert slots == [0, 1, 2, 0]
    assert "secret-" not in str(pool.status(now=0))


def test_per_token_concurrency_limit_is_enforced() -> None:
    pool = MinerUTokenPool(
        ["secret-a", "secret-b"],
        max_concurrency_per_token=1,
        max_failures=2,
        cooldown_seconds=30,
    )
    first = pool.acquire(now=0)
    second = pool.acquire(now=0)

    with pytest.raises(MinerURetryableError) as error:
        pool.acquire(now=0)
    assert error.value.code == "MINERU_TOKENS_UNAVAILABLE"
    first.release()
    second.release()


def test_429_cools_token_and_fails_over_without_leaking_token() -> None:
    pool = _pool()
    called: list[str] = []

    def operation(token: str) -> str:
        called.append(token)
        if token == "secret-a":  # noqa: S105
            raise MinerURateLimitError("429")
        return "ok"

    assert pool.call(operation, now=10) == "ok"
    assert called == ["secret-a", "secret-b"]
    status = pool.status(now=10)
    assert status["slots"][0]["cooling_down"] is True
    assert "secret-a" not in str(status)


def test_consecutive_failures_trigger_cooldown_and_all_unavailable_is_retryable() -> None:
    pool = MinerUTokenPool(
        ["secret-a"],
        max_concurrency_per_token=1,
        max_failures=2,
        cooldown_seconds=20,
    )

    def fail(_token: str) -> str:
        raise RuntimeError("provider failure without credential")

    with pytest.raises(MinerURetryableError):
        pool.call(fail, now=0)
    with pytest.raises(MinerURetryableError):
        pool.call(fail, now=1)
    with pytest.raises(MinerURetryableError) as cooling:
        pool.acquire(now=2)
    assert cooling.value.retry_after_seconds == 19


def test_concurrent_acquisition_never_exceeds_slot_limit() -> None:
    pool = MinerUTokenPool(
        ["secret-a", "secret-b"],
        max_concurrency_per_token=1,
        max_failures=2,
        cooldown_seconds=20,
    )
    barrier = threading.Barrier(3)
    slots: list[int] = []

    def hold() -> None:
        with pool.acquire(now=0) as lease:
            slots.append(lease.slot)
            barrier.wait()
            barrier.wait()

    threads = [threading.Thread(target=hold) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    assert sorted(slots) == [0, 1]
    with pytest.raises(MinerURetryableError):
        pool.acquire(now=0)
    barrier.wait()
    for thread in threads:
        thread.join()
