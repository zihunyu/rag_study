from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from ragkb.application.qa_performance import (
    performance_report,
    performance_scope,
    record_event,
    request_identity,
    summarize_performance,
    timed_stage,
)
from ragkb.application.tracing import InMemoryTracer


def test_parallel_events_keep_stage_context_without_storing_request_content():
    secret = {"model": "test", "messages": [{"content": "private-answer-content"}]}
    with performance_scope():
        with InMemoryTracer().span("rag.ask.claim.verify"):

            def batch():
                with timed_stage("verification.conditions"):
                    for _ in range(2):
                        record_event(
                            "model_http", request_id=request_identity(secret), outcome="200"
                        )

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(copy_context().run, batch).result()
        data = performance_report()
    assert "private-answer-content" not in str(data)
    calls = summarize_performance(data)["calls"]
    assert all(c["stage"] == "rag.ask.claim.verify / verification.conditions" for c in calls)
    assert [c["same_request_again"] for c in calls] == [False, True]
    with performance_scope():
        assert request_identity({"different": "payload"}) == "R1"


def test_old_timings_do_not_double_count_children_or_claim_duplicate_identity():
    summary = summarize_performance(
        {
            "elapsed_seconds": 100,
            "events": [
                {"kind": "stage", "name": "rag.ask.claim.verify", "seconds": 60},
                {"kind": "stage", "name": "verification.conditions", "seconds": 40},
                {"kind": "stage", "name": "verification.conditions", "seconds": 30},
                {"kind": "stage", "name": "rag.ask.claim.reverify", "seconds": 20},
                {"kind": "model_http", "model": "test", "outcome": "failed", "network_seconds": 60},
            ],
        }
    )
    assert sum(s["seconds"] for s in summary["stages"]) == 80
    assert summary["other_seconds"] == 20
    assert summary["reverification_count"] == 1 and summary["failed_calls"] == 1
    assert not summary["calls"][0]["identity_recorded"]
    assert not summarize_performance({})


def test_verification_work_distinguishes_receipt_retry_repair_and_duplicate_removal():
    paths = [
        ["rag.ask.claim.verify", "verification.conditions"],
        ["rag.ask.claim.verify", "verification.conditions.protocol_repair"],
        ["rag.ask.citations.repair"],
        ["rag.ask.citations.reverify"],
    ]
    summary = summarize_performance(
        {
            "elapsed_seconds": 30,
            "events": [
                *[{"kind": "model_http", "stage_path": p, "outcome": "200"} for p in paths],
                {"kind": "answer_projection", "outcome": "removed_verified_duplicate_table"},
                {"kind": "citation_assembly", "outcome": "exact_ledger_binding"},
                {
                    "kind": "retrieval_round",
                    "number": 2,
                    "new_sources": 0,
                    "reason": "supplement_missing",
                },
            ],
        }
    )
    assert summary["verification_work"] == {
        "initial_calls": 1,
        "protocol_retry_calls": 1,
        "answer_repair_calls": 1,
        "verified_duplicate_removals": 1,
        "citation_assemblies": 1,
    }
    assert summary["retrieval_rounds"][0]["new_sources"] == 0
