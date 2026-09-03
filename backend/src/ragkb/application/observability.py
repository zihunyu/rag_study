"""Local trace, structured-event, metric and alert adapter."""

from __future__ import annotations

from ragkb.application.tracing import InMemoryTracer
from ragkb.contracts.governance import GovernanceRepositoryPort


class LocalObservabilityService:
    revision = "local-observability:g5-v1"

    def __init__(
        self, repository: GovernanceRepositoryPort, tracer: InMemoryTracer | None = None
    ) -> None:
        self.repository = repository
        self.tracer = tracer or InMemoryTracer()

    def request_completed(self, trace_id: str, method: str, path: str, status_code: int) -> None:
        self.repository.record_event(
            trace_id,
            "http.request.completed",
            "ERROR" if status_code >= 500 else "WARNING" if status_code >= 400 else "INFO",
            {"method": method, "path": path, "status_code": status_code},
        )

    def diagnostics(self) -> dict[str, object]:
        return {
            **self.repository.diagnostics(),
            "revision": self.revision,
            "simulated": True,
            "real_acceptance": False,
            "rag_tracing": self.tracer.summary(),
        }

    def alerts(self) -> list[dict[str, object]]:
        diagnostics = self.repository.diagnostics()
        severities = diagnostics["events_by_severity"]
        errors = int(severities.get("ERROR", 0)) if isinstance(severities, dict) else 0
        return [
            {
                "code": "LOCAL_HTTP_5XX",
                "active": errors > 0,
                "count": errors,
                "simulated": True,
            },
            {
                "code": "EXTERNAL_EXPORT_DISABLED",
                "active": False,
                "count": 0,
                "simulated": True,
            },
        ]
