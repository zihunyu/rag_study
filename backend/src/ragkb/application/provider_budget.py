"""Atomic, fail-closed budget ledger for explicitly approved real-provider acceptance."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class ProviderBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderBudgetLimits:
    max_calls: int = 60
    max_input_tokens: int = 200_000
    max_output_tokens: int = 20_000

    def __post_init__(self) -> None:
        if min(self.max_calls, self.max_input_tokens, self.max_output_tokens) < 1:
            raise ValueError("provider budget limits must be positive")


@dataclass(frozen=True)
class ProviderBudgetUsage:
    calls: int
    input_tokens: int
    output_tokens: int


class TokenCounterPort(Protocol):
    revision: str

    def count(self, value: str) -> int: ...


class ConservativeTokenCounter:
    """Local upper-bound estimate used when a provider omits usage metadata."""

    revision = "conservative-unicode-token-counter:v1"

    def count(self, value: str) -> int:
        # Counting every non-ASCII code point and every two ASCII characters is deliberately
        # conservative for the configured acceptance budget.
        ascii_count = sum(character.isascii() for character in value)
        return max(1, len(value) - ascii_count + (ascii_count + 1) // 2)


class ProviderBudgetLedgerPort(Protocol):
    def reserve(
        self, provider_role: str, operation: str, input_tokens: int, output_tokens: int
    ) -> str: ...

    def complete(
        self, reservation_id: str, *, actual_input_tokens: int, actual_output_tokens: int
    ) -> None: ...

    def fail(self, reservation_id: str) -> None: ...


class JsonTransportPort(Protocol):
    real_network: bool

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]: ...


class BudgetedJsonTransport:
    """Reserve count and tokens before every real provider request."""

    def __init__(
        self,
        inner: JsonTransportPort,
        ledger: ProviderBudgetLedgerPort,
        *,
        provider_role: str,
        token_counter: TokenCounterPort | None = None,
    ) -> None:
        self.inner = inner
        self.ledger = ledger
        self.provider_role = provider_role
        self.token_counter = token_counter or ConservativeTokenCounter()
        self.real_network = inner.real_network

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        reserved_input = self.token_counter.count(serialized)
        reserved_output = max(0, int(payload.get("max_tokens", 0)))
        reservation = self.ledger.reserve(
            self.provider_role,
            "post_json",
            reserved_input,
            reserved_output,
        )
        try:
            response = self.inner.post_json(url, headers=headers, payload=payload, timeout=timeout)
        except Exception:
            self.ledger.fail(reservation)
            raise
        usage = response.get("usage")
        actual_input = reserved_input
        actual_output = reserved_output
        if isinstance(usage, Mapping):
            actual_input = min(
                reserved_input,
                max(0, int(usage.get("prompt_tokens", reserved_input))),
            )
            actual_output = min(
                reserved_output,
                max(0, int(usage.get("completion_tokens", reserved_output))),
            )
        self.ledger.complete(
            reservation,
            actual_input_tokens=actual_input,
            actual_output_tokens=actual_output,
        )
        return response
