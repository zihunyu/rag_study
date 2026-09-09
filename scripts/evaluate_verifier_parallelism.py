"""Bounded A/B experiment using frozen private evidence; never publishes answers."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.model_http import (  # noqa: E402
    HttpxJsonTransport,
    OpenAICompatibleClaimVerifier,
)
from ragkb.application.acceptance_budget import acceptance_budget  # noqa: E402
from ragkb.config import load_env  # noqa: E402
from ragkb.domain.answer_conditions import condition_requirements  # noqa: E402
from ragkb.domain.rag import AtomicClaim, DraftAnswer  # noqa: E402
from ragkb.evaluation.verifier_parallelism import parallel_verify  # noqa: E402
from ragkb.infrastructure.model_account import provider_operation  # noqa: E402
from ragkb.infrastructure.rag_repository import _package  # noqa: E402

_timing: ContextVar[dict | None] = ContextVar("experiment_call_timing", default=None)


class MeasuredLimiter:
    def __init__(self, inner):
        self.inner = inner
        self.redis = inner.redis

    @contextmanager
    def reserve(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            with self.inner.reserve(*args, **kwargs) as lease:
                record = _timing.get()
                if record is not None:
                    record["quota_wait_seconds"] += time.perf_counter() - started
                yield lease
        except BaseException:
            raise

    def settle(self, *args, **kwargs):
        return self.inner.settle(*args, **kwargs)

    def available_workers(self, *args, **kwargs):
        return self.inner.available_workers(*args, **kwargs)


class MeasuredTransport(HttpxJsonTransport):
    def __init__(self, settings):
        super().__init__(settings)
        if self._account:
            self._account = MeasuredLimiter(self._account)
        self.calls = []
        self.lock = threading.Lock()
        self.started = time.perf_counter()

    def post_json(self, url, *, headers, payload, timeout):
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        body = json.loads(payload["messages"][-1]["content"])
        record = {
            "payload_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
            "input_characters": len(rendered),
            "condition_ids": [r["id"] for r in body["condition_requirements"]],
            "conflict_source_ids": [r["evidence_id"] for r in body.get("conflict_evidence", [])],
            "protocol_repair": bool(body.get("protocol_repair")),
            "start_seconds": time.perf_counter() - self.started,
            "quota_wait_seconds": 0.0,
            "payload": payload,
        }
        token = _timing.set(record)
        started = time.perf_counter()
        try:
            response = super().post_json(url, headers=headers, payload=payload, timeout=timeout)
            record["response"] = dict(response)
            record["usage"] = response.get("usage", {})
            return response
        except Exception as error:
            record["error"] = getattr(error, "code", type(error).__name__)
            raise
        finally:
            record["elapsed_seconds"] = time.perf_counter() - started
            _timing.reset(token)
            with self.lock:
                self.calls.append(record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--order", choices=("1", "2", "3", "1,3", "3,1"), default="1,3")
    parser.add_argument("--max-calls", type=int, default=24)
    parser.add_argument("--settle-window", type=int, default=0, choices=(0, 65))
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    settings = load_env(ROOT).settings
    if not args.live or not settings.real_provider_calls_enabled:
        parser.error("Use --live with the project's enabled real-provider setting.")
    if not 1 <= args.max_calls <= 40:
        parser.error("max-calls must be between 1 and 40")
    frozen = json.loads(args.fixture.read_text(encoding="utf-8"))
    package = _package(frozen["package"])
    raw = frozen["draft"]
    if not raw:
        parser.error("Frozen fixture must contain the original cached verified draft.")
    draft = DraftAnswer(
        raw["text"],
        tuple(raw["citation_ids"]),
        tuple(
            AtomicClaim(c["text"], tuple(c["evidence_ids"]), tuple(c.get("visual_fact_ids", [])))
            for c in raw["claims"]
        ),
        synthesized=raw.get("synthesized", False),
    )
    counter, lock = [0], threading.Lock()

    def reserve():
        with lock:
            if counter[0] >= args.max_calls:
                raise RuntimeError("LATENCY_EXPERIMENT_CALL_BUDGET_EXHAUSTED")
            counter[0] += 1

    report = {
        "question": package.query,
        "frozen_fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        "run_id": package.rag_run_id,
        "scope": "verifier only; fixed draft and complete evidence; no production change",
        "conditions": len(condition_requirements(package.evidence)),
        "evidence_count": len(package.evidence),
        "variants": [],
        "max_provider_calls": args.max_calls,
        "settle_window_seconds": args.settle_window,
    }
    private = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for workers in map(int, args.order.split(",")):
        if args.settle_window:
            print(
                json.dumps({"event": "quota_window_settling", "seconds": args.settle_window}),
                flush=True,
            )
            until = time.monotonic() + args.settle_window
            while time.monotonic() < until:
                time.sleep(min(5, until - time.monotonic()))
        transport = MeasuredTransport(settings)
        verifier = OpenAICompatibleClaimVerifier(
            settings,
            transport=transport,
            external_call_approved=True,
        )
        started = time.perf_counter()
        outcome = {"workers": workers, "revision": verifier.revision}
        print(json.dumps({"event": "variant_started", "workers": workers}), flush=True)
        try:
            with (
                acceptance_budget(reserve, lambda _: None),
                provider_operation("", "", "latency_evaluation"),
            ):
                result = parallel_verify(
                    verifier,
                    package.query,
                    draft,
                    package.evidence,
                    workers=workers,
                )
            outcome["result"] = asdict(result)
            outcome["supported"] = result.supported
        except Exception as error:
            outcome["error"] = getattr(error, "code", type(error).__name__)
            outcome["error_detail"] = getattr(error, "diagnostic", {})
        finally:
            outcome["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            outcome["calls"] = [
                {k: v for k, v in call.items() if k not in {"payload", "response"}}
                for call in sorted(transport.calls, key=lambda c: c["start_seconds"])
            ]
            private.append({"workers": workers, "calls": transport.calls})
            transport.close()
        report["variants"].append(outcome)
        report["provider_calls"] = counter[0]
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        args.output.with_name(args.output.stem + "-private.json").write_text(
            json.dumps(private, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {k: v for k, v in outcome.items() if k not in {"calls", "result"}},
                ensure_ascii=False,
            ),
            flush=True,
        )
    if len(report["variants"]) == 1:
        return int("error" in report["variants"][0])
    left, right = sorted(report["variants"], key=lambda row: row["workers"])
    report["identical_initial_requests"] = Counter(
        c["payload_sha256"] for c in left["calls"] if not c["protocol_repair"]
    ) == Counter(c["payload_sha256"] for c in right["calls"] if not c["protocol_repair"])

    def signature(row):
        if "result" not in row:
            return None
        value = row["result"]
        return {
            "supported": row["supported"],
            "verdicts": [
                (v["claim_text"], v["evidence_ids"], v["verdict"]) for v in value["verdicts"]
            ],
            "conflicts": value["conflicting_evidence_ids"],
            "conditions": [
                (
                    c["id"],
                    c["status"],
                    c["evidence_id"],
                    c["source_quote"],
                    c.get("answer_quote", ""),
                )
                for c in value["condition_checks"]
            ],
        }

    report["same_verdicts_conditions_and_witnesses"] = signature(left) is not None and signature(
        left
    ) == signature(right)
    report["speedup"] = round(left["elapsed_seconds"] / right["elapsed_seconds"], 3)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "provider_calls",
                    "identical_initial_requests",
                    "same_verdicts_conditions_and_witnesses",
                    "speedup",
                )
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
