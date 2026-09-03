from __future__ import annotations

from ragkb.application.tracing import InMemoryTracer


def test_tracer_records_parent_child_duration_and_status() -> None:
    tracer = InMemoryTracer()
    with tracer.span("rag.ask"):
        with tracer.span("rag.ask.llm.generate"):
            pass

    root = next(item for item in tracer.completed if item.name == "rag.ask")
    child = next(item for item in tracer.completed if item.name.endswith("generate"))
    assert root.trace_id == child.trace_id
    assert child.parent_span_id == root.span_id
    assert tracer.summary()["span_count"] == 2
