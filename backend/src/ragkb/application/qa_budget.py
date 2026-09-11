"""One thread-safe budget for a question, including HTTP retries and child contexts."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Literal

from ragkb.application.deadlines import remaining_timeout, request_deadline
from ragkb.application.provider_budget import ConservativeTokenCounter
from ragkb.application.query_planning import plan_queries
from ragkb.application.reading_scope import is_overview, options
from ragkb.config import EnvSettings
from ragkb.domain.errors import QABudgetExceeded

Profile = Literal["simple", "standard", "deep"]
Stage = Literal["retrieval", "generation", "verification"]


def choose_profile(question: str) -> Profile:
    requested = options.get().budget_profile
    if requested != "auto":
        return requested
    # Unknown/compound questions stay standard; this classifier makes no model call.
    if (
        len(question.strip()) <= 100
        and len(plan_queries(question)) == 1
        and not is_overview(question)
        and not re.search(
            r"比较|对比|区别|分别|以及|并且|同时|为什么|如何|原因|影响|分析|计算|"
            r"所有|哪些|流程|步骤|是否|能否|和|与|及|、|\b(and|compare|why|how|all|can)\b",
            question,
            re.I,
        )
        and re.search(r"多少|多久|何时|哪里|谁|是什么|\b(what|when|where|who)\b", question, re.I)
    ):
        return "simple"
    return "standard"


@dataclass(frozen=True)
class Limits:
    calls: int
    input_tokens: int
    output_tokens: int
    seconds: float


@dataclass(frozen=True)
class Reservation:
    input_tokens: int
    output_tokens: int


@dataclass
class QuestionBudget:
    profile: Profile
    limits: Limits
    started: float = field(default_factory=time.monotonic)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    unknown_usage_calls: int = 0
    stopped_reason: str | None = None
    stopped_stage: str | None = None
    lock: Lock = field(default_factory=Lock)
    max_queries: int = 4
    queries: list[str] = field(default_factory=list)

    def start_query(self, query: str) -> None:
        self.start_queries((query,))

    def start_queries(self, queries: tuple[str, ...]) -> None:
        with self.lock:
            self.timeout(self.limits.seconds)
            if len(self.queries) + len(queries) > self.max_queries:
                self.stop("QA_RETRIEVAL_BUDGET_EXHAUSTED")
            self.queries.extend(queries)

    def stop(self, code: str) -> None:
        self.stopped_reason, self.stopped_stage = code, stage.get()
        raise QABudgetExceeded(code)

    @staticmethod
    def protected_fraction() -> float:
        # Retrieval protects generation (15%) AND verification (35%). Generation
        # can use the released retrieval pool, but cannot spend verification's pool.
        return {"retrieval": 0.50, "generation": 0.35, "verification": 0.0}[stage.get()]

    def timeout(self, requested: float) -> float:
        protected = self.limits.seconds * self.protected_fraction()
        available = self.limits.seconds - (time.monotonic() - self.started) - protected
        if available <= 0:
            self.stop("QA_TIME_BUDGET_EXHAUSTED")
        return remaining_timeout(min(requested, available))

    def reserve(self, payload: Mapping[str, Any]) -> Reservation:
        # Image data is not text tokens. Keep a conservative per-image allowance;
        # actual provider usage always replaces the estimate without clipping.
        def sanitized(value: Any) -> Any:
            if isinstance(value, dict):
                if value.get("type") == "image_url":
                    return "image " * 8192
                return {k: sanitized(v) for k, v in value.items()}
            if isinstance(value, list):
                return [sanitized(v) for v in value]
            return value

        estimated = ConservativeTokenCounter().count(
            json.dumps(sanitized(dict(payload)), ensure_ascii=False)
        )
        input_tokens = math.ceil(estimated * 1.5) + 256
        output = payload.get("max_completion_tokens", payload.get("max_tokens", 0))
        if "messages" in payload and (type(output) is not int or output <= 0):
            self.stop("QA_OUTPUT_LIMIT_REQUIRED")
        output_tokens = output if type(output) is int and output > 0 else 0
        with self.lock:
            self.timeout(self.limits.seconds)
            fraction = 1 - self.protected_fraction()
            for used, requested, maximum, reason in (
                (self.calls, 1, self.limits.calls, "QA_MODEL_CALL_BUDGET_EXHAUSTED"),
                (
                    self.input_tokens,
                    input_tokens,
                    self.limits.input_tokens,
                    "QA_INPUT_BUDGET_EXHAUSTED",
                ),
                (
                    self.output_tokens,
                    output_tokens,
                    self.limits.output_tokens,
                    "QA_OUTPUT_BUDGET_EXHAUSTED",
                ),
            ):
                if used + requested > math.floor(maximum * fraction):
                    self.stop(reason)
            self.calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
        return Reservation(input_tokens, output_tokens)

    def settle(self, reservation: Reservation, usage: Mapping[str, Any], *, sent: bool) -> None:
        with self.lock:
            if not sent:
                self.calls -= 1
                self.input_tokens -= reservation.input_tokens
                self.output_tokens -= reservation.output_tokens
                return
            actual_input = usage.get("prompt_tokens", usage.get("input_tokens"))
            actual_output = usage.get("completion_tokens", usage.get("output_tokens"))
            known_input = type(actual_input) is int and actual_input >= 0
            known_output = type(actual_output) is int and actual_output >= 0
            if known_input:
                self.input_tokens += actual_input - reservation.input_tokens
            if known_output:
                self.output_tokens += actual_output - reservation.output_tokens
            if not (known_input and (known_output or reservation.output_tokens == 0)):
                self.unknown_usage_calls += 1

    def report(self) -> dict[str, Any]:
        with self.lock:
            return {
                "revision": "qa-budget-v1",
                "profile": self.profile,
                "model_calls": self.calls,
                "max_model_calls": self.limits.calls,
                "retrieval_queries_used": len(self.queries),
                "max_retrieval_queries": self.max_queries,
                "input_tokens": self.input_tokens,
                "max_input_tokens": self.limits.input_tokens,
                "output_tokens": self.output_tokens,
                "max_output_tokens": self.limits.output_tokens,
                "unknown_usage_calls": self.unknown_usage_calls,
                "token_accounting": "provider_usage_or_reserved_estimate",
                "elapsed_seconds": round(time.monotonic() - self.started, 3),
                "max_seconds": self.limits.seconds,
                "stopped_reason": self.stopped_reason,
                "stopped_stage": self.stopped_stage,
            }


current: ContextVar[QuestionBudget | None] = ContextVar("qa_budget", default=None)
stage: ContextVar[Stage] = ContextVar("qa_budget_stage", default="retrieval")


@contextmanager
def budget_stage(value: Stage) -> Iterator[None]:
    token = stage.set(value)
    try:
        yield
    finally:
        stage.reset(token)


@contextmanager
def question_budget(settings: EnvSettings, question: str) -> Iterator[QuestionBudget]:
    existing = current.get()
    if existing is not None:
        yield existing
        return
    profile = choose_profile(question)
    prefix = "qa_budget_" + profile + "_"
    limits = Limits(
        getattr(settings, prefix + "max_model_calls"),
        getattr(settings, prefix + "max_input_tokens"),
        getattr(settings, prefix + "max_output_tokens"),
        remaining_timeout(float(getattr(settings, prefix + "timeout_seconds"))),
    )
    maximum = (
        settings.retrieval_deep_max_subqueries
        if profile == "deep"
        else settings.retrieval_max_subqueries
    )
    if profile == "simple":
        maximum = min(2, maximum)
    if not settings.retrieval_query_planning_enabled:
        maximum = 1
    budget = QuestionBudget(profile, limits, max_queries=maximum)
    token = current.set(budget)
    try:
        with budget_stage("retrieval"), request_deadline(limits.seconds, check_on_exit=False):
            yield budget
    finally:
        current.reset(token)
