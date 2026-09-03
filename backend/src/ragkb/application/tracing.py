"""Vendor-neutral nested spans with an optional OpenTelemetry bridge."""

from __future__ import annotations

import contextvars
import importlib
import secrets
import time
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CompletedSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    duration_seconds: float
    status: str
    attributes: Mapping[str, object]


class TracerPort(Protocol):
    def span(self, name: str, attributes: Mapping[str, object] | None = None) -> Any: ...


class InMemoryTracer:
    """Records the same span tree locally that an OTLP exporter receives in production."""

    revision = "rag-tracing:v1"

    def __init__(self) -> None:
        self.completed: list[CompletedSpan] = []
        self._current: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
            "rag_current_span", default=None
        )

    @contextmanager
    def span(self, name: str, attributes: Mapping[str, object] | None = None) -> Iterator[None]:
        parent = self._current.get()
        trace_id = parent[0] if parent else secrets.token_hex(16)
        span_id = secrets.token_hex(8)
        token = self._current.set((trace_id, span_id))
        started = time.perf_counter()
        status = "OK"
        try:
            yield
        except BaseException:
            status = "ERROR"
            raise
        finally:
            duration = time.perf_counter() - started
            self._current.reset(token)
            self.completed.append(
                CompletedSpan(
                    trace_id,
                    span_id,
                    parent[1] if parent else None,
                    name,
                    duration,
                    status,
                    dict(attributes or {}),
                )
            )

    def summary(self) -> dict[str, object]:
        by_name: dict[str, list[float]] = {}
        for span in self.completed:
            by_name.setdefault(span.name, []).append(span.duration_seconds)
        return {
            "span_count": len(self.completed),
            "error_count": sum(span.status == "ERROR" for span in self.completed),
            "latency_by_span": {
                name: {
                    "count": len(values),
                    "max_seconds": max(values),
                    "mean_seconds": sum(values) / len(values),
                }
                for name, values in sorted(by_name.items())
            },
        }


class OpenTelemetryTracer:
    """Thin bridge around an SDK tracer configured by the deployment."""

    revision = "opentelemetry-rag-tracing:v1"

    def __init__(self, tracer: Any) -> None:
        self._tracer = tracer

    @contextmanager
    def span(self, name: str, attributes: Mapping[str, object] | None = None) -> Iterator[None]:
        with self._tracer.start_as_current_span(name, attributes=dict(attributes or {})):
            yield


class CompositeTracer:
    def __init__(self, tracers: tuple[TracerPort, ...]) -> None:
        self.tracers = tracers

    @contextmanager
    def span(self, name: str, attributes: Mapping[str, object] | None = None) -> Iterator[None]:
        with ExitStack() as stack:
            for tracer in self.tracers:
                stack.enter_context(tracer.span(name, attributes))
            yield


def build_runtime_tracer(
    *, enabled: bool, endpoint: str, service_name: str
) -> tuple[TracerPort, InMemoryTracer]:
    """Return a local recorder plus OTLP export when the optional SDK is installed."""

    local = InMemoryTracer()
    if not enabled:
        return local, local
    if not endpoint.strip():
        raise ValueError("OTEL_EXPORTER_OTLP_ENDPOINT_REQUIRED")
    try:
        trace_api = importlib.import_module("opentelemetry.trace")
        sdk_trace = importlib.import_module("opentelemetry.sdk.trace")
        sdk_export = importlib.import_module("opentelemetry.sdk.trace.export")
        resource_module = importlib.import_module("opentelemetry.sdk.resources")
        exporter_module = importlib.import_module(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        )
    except ImportError as error:
        raise RuntimeError("OPENTELEMETRY_OPTIONAL_DEPENDENCY_REQUIRED") from error
    provider = sdk_trace.TracerProvider(
        resource=resource_module.Resource.create({"service.name": service_name})
    )
    provider.add_span_processor(
        sdk_export.BatchSpanProcessor(exporter_module.OTLPSpanExporter(endpoint=endpoint))
    )
    trace_api.set_tracer_provider(provider)
    exported = OpenTelemetryTracer(trace_api.get_tracer("ragkb", "1"))
    return CompositeTracer((local, exported)), local
