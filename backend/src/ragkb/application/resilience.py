"""Local cost metering and circuit-breaker contracts without billable requests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class LocalCircuitBreaker:
    failure_threshold: int
    state: CircuitState = CircuitState.CLOSED
    failures: int = 0

    def allow(self) -> bool:
        return self.state is not CircuitState.OPEN

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN

    def begin_probe(self) -> None:
        if self.state is CircuitState.OPEN:
            self.state = CircuitState.HALF_OPEN

    def record_success(self) -> None:
        self.failures = 0
        self.state = CircuitState.CLOSED


@dataclass
class DryRunCostMeter:
    input_units: int = 0
    output_units: int = 0
    request_count: int = 0

    def record(self, *, input_units: int, output_units: int) -> None:
        self.input_units += input_units
        self.output_units += output_units
        self.request_count += 1

    def report(self) -> dict[str, int | bool]:
        return {
            "request_count": self.request_count,
            "input_units": self.input_units,
            "output_units": self.output_units,
            "billable_request_performed": False,
        }
