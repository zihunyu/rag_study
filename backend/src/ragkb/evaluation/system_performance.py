"""Execute representative local system paths with synthetic data and no external calls."""

from __future__ import annotations

import hashlib
import platform
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
from httpx import Response

from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker
from ragkb.document_processing.parsers import ParserRouter
from ragkb.runtime_components import build_runtime_components


def _timed_post(
    client: TestClient,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, object] | None = None,
) -> tuple[Response, float]:
    started = time.perf_counter()
    response = client.post(path, headers=headers, json=json_body)
    return response, time.perf_counter() - started


def _summary(latencies: list[float]) -> dict[str, float | int]:
    ordered = sorted(latencies)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "count": len(ordered),
        "mean_seconds": statistics.fmean(ordered),
        "max_seconds": ordered[-1],
        "p95_seconds": ordered[p95_index],
    }


def run_representative_system_paths(
    root: Path,
    *,
    document_scales: Sequence[int] = (1, 5, 20),
    concurrency_values: Sequence[int] = (1, 2),
) -> dict[str, object]:
    del root
    scale_results: list[dict[str, object]] = []
    total_success = 0
    total_failure = 0
    all_latencies: list[float] = []
    if (
        not document_scales
        or not concurrency_values
        or min(*document_scales, *concurrency_values) < 1
    ):
        raise ValueError("performance scales and concurrency must be positive")
    for scale in document_scales:
        with tempfile.TemporaryDirectory(prefix=f"ragkb-g4-perf-{scale}-") as temporary:
            temp = Path(temporary)
            components = build_runtime_components(
                storage_root=temp / "storage",
                database_path=temp / "control.sqlite3",
            )
            app = create_app(components)
            with TestClient(app) as client:
                version_ids: list[str] = []
                worker = LocalIngestionWorker(
                    components.queue,
                    components.repository,
                    components.storage,
                    ParserRouter(),
                    f"perf-worker-{scale}",
                    chunker=components.chunker,
                    indexing_sink=components.indexing_sink,
                )
                for index in range(scale):
                    content = f"synthetic document {index} at scale {scale}".encode()
                    created, elapsed = _timed_post(
                        client,
                        f"/api/v1/spaces/{components.space_id}/upload-sessions",
                        headers={"Idempotency-Key": f"perf-create-{scale}-{index}"},
                        json_body={
                            "filename": f"perf-{scale}-{index}.txt",
                            "expected_size": len(content),
                            "expected_sha256": hashlib.sha256(content).hexdigest(),
                            "declared_mime": "text/plain",
                        },
                    )
                    all_latencies.append(elapsed)
                    uploaded = client.put(
                        created.json()["upload_path"],
                        headers={"If-Match": created.headers["etag"]},
                        content=content,
                    )
                    completed = client.post(
                        f"/api/v1/upload-sessions/{created.json()['upload_session_id']}:complete",
                        headers={
                            "If-Match": uploaded.headers["etag"],
                            "Idempotency-Key": f"perf-complete-{scale}-{index}",
                        },
                    )
                    version_ids.append(completed.json()["document_version_id"])
                for index, version_id in enumerate(version_ids):
                    assert worker.run_once()
                    client.post(
                        f"/api/v1/document-versions/{version_id}/review",
                        headers={"Idempotency-Key": f"perf-review-{scale}-{index}"},
                        json={
                            "decision": "APPROVED",
                            "comment": "synthetic performance",
                            "security_projection": {
                                "visibility": "TENANT",
                                "classification_level": 0,
                                "acl_scope_tokens": [],
                            },
                        },
                    )
                    published, elapsed = _timed_post(
                        client,
                        f"/api/v1/document-versions/{version_id}:publish",
                        headers={"Idempotency-Key": f"perf-publish-{scale}-{index}"},
                    )
                    all_latencies.append(elapsed)
                    if published.status_code == 200:
                        total_success += 1
                    else:
                        total_failure += 1

                request_results: list[tuple[int, float, int]] = []
                for concurrency in concurrency_values:
                    for top_k in (1, 5):

                        def representative_request(
                            index: int, *, top_k: int = top_k, client: TestClient = client
                        ) -> tuple[int, float, int]:
                            if index % 2:
                                response, elapsed = _timed_post(
                                    client,
                                    "/api/v1/ask",
                                    json_body={"question": "synthetic question"},
                                )
                                answer_length = len(response.json().get("answer") or "")
                            else:
                                response, elapsed = _timed_post(
                                    client,
                                    "/api/v1/search",
                                    json_body={"query": "synthetic", "limit": top_k},
                                )
                                answer_length = 0
                            return response.status_code, elapsed, answer_length

                        with ThreadPoolExecutor(max_workers=concurrency) as executor:
                            request_results.extend(
                                executor.map(representative_request, range(concurrency * 2))
                            )
                long_run_success = 0
                long_run_failure = 0
                long_run_latencies: list[float] = []
                for iteration in range(25):
                    response, elapsed = _timed_post(
                        client,
                        "/api/v1/search" if iteration % 2 == 0 else "/api/v1/ask",
                        json_body=(
                            {"query": "long run", "limit": 5}
                            if iteration % 2 == 0
                            else {"question": "long run question"}
                        ),
                    )
                    long_run_latencies.append(elapsed)
                    if response.status_code == 200:
                        long_run_success += 1
                    else:
                        long_run_failure += 1
                request_latencies = [item[1] for item in request_results]
                all_latencies.extend(request_latencies)
                all_latencies.extend(long_run_latencies)
                total_success += sum(item[0] == 200 for item in request_results) + long_run_success
                total_failure += sum(item[0] != 200 for item in request_results) + long_run_failure
                scale_results.append(
                    {
                        "document_count": scale,
                        "concurrency_values": list(concurrency_values),
                        "top_k_values": [1, 5],
                        "representative_request_count": len(request_results),
                        "observed_answer_lengths": [item[2] for item in request_results],
                        "request_latency": _summary(request_latencies),
                        "long_run": {
                            "count": 25,
                            "success": long_run_success,
                            "failure": long_run_failure,
                            "latency": _summary(long_run_latencies),
                        },
                    }
                )
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "scales": scale_results,
        "success_count": total_success,
        "failure_count": total_failure,
        "overall_latency": _summary(all_latencies),
        "slo_claimed": False,
        "statistical_confidence": "low",
        "performance_scope": list(document_scales),
        "real_external_call_performed": False,
    }
