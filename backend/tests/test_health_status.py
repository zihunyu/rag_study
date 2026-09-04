from __future__ import annotations

import time
from dataclasses import replace

from fastapi.testclient import TestClient
from ragkb.adapters.model_http import HttpxJsonTransport
from ragkb.api.app import create_app
from ragkb.runtime_components import build_runtime_components


def test_health_uses_loaded_acceptance_and_checks_local_disk(tmp_path) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    components.search_service.real_acceptance = True
    client = TestClient(create_app(components))

    assert client.get("/health/live").json()["real_service_acceptance"] is True
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["dependencies"]["storage"]["state"] == "ready"

    constrained = replace(
        components,
        settings=components.settings.model_copy(update={"local_storage_min_free_gb": 10**9}),
    )
    response = TestClient(create_app(constrained)).get("/health/ready")
    assert response.status_code == 503
    assert "LOCAL_STORAGE_FREE_SPACE_LOW" in response.json()["degraded_reasons"]


def test_degraded_status_uses_cached_circuit_state_without_provider_call(tmp_path) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    transport = HttpxJsonTransport(components.settings)
    transport._consecutive_failures = components.settings.model_http_circuit_failure_threshold
    transport._circuit_opened_at = time.monotonic()
    runtime = replace(components, provider_transports=(transport,))
    client = TestClient(create_app(runtime))

    response = client.get("/status/degraded")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["dependencies"]["providers"]["embedding"]["request_count"] == 0
    assert "EMBEDDING_CIRCUIT_OPEN" in response.json()["degraded_reasons"]
    transport.close()
