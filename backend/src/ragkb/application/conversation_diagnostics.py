"""Public, content-free failures for stages before a RAG package exists."""

from typing import Any

from ragkb.domain.errors import TransientProviderError

PUBLIC_CONVERSATION_CODES = frozenset(
    {
        "MODEL_PROVIDER_RATE_LIMITED",
        "MODEL_ACCOUNT_REQUEST_EXCEEDS_TOKEN_BUDGET",
        "MODEL_ACCOUNT_QUOTA_WAIT_TIMEOUT",
        "MODEL_ACCOUNT_COORDINATOR_UNAVAILABLE",
        "MODEL_PROVIDER_TIMEOUT",
        "MODEL_PROVIDER_DEADLINE_EXCEEDED",
        "REQUEST_DEADLINE_EXCEEDED",
        "MODEL_CONCURRENCY_WAIT_TIMEOUT",
        "MODEL_PROVIDER_HTTP_ERROR",
        "MODEL_PROVIDER_MODEL_UNSUPPORTED",
        "MODEL_PROVIDER_AUTHENTICATION_FAILED",
        "MODEL_PROVIDER_NETWORK_UNAVAILABLE",
        "MODEL_PROVIDER_TEMPORARILY_UNAVAILABLE",
        "MODEL_PROVIDER_CIRCUIT_OPEN",
        "CONVERSATION_CONTEXT_INVALID",
        "CONVERSATION_EXECUTION_FAILED",
    }
)


def conversation_failure(
    error: Exception, stage: str, turn_id: str, performance: dict[str, Any]
) -> dict[str, Any]:
    raw_code = getattr(error, "code", "")
    code = raw_code if raw_code in PUBLIC_CONVERSATION_CODES else "CONVERSATION_EXECUTION_FAILED"
    detail: dict[str, Any] = {"stage": stage, "code": code, "request_id": turn_id}
    status = getattr(error, "diagnostic", {}).get("http_status")
    if code == "MODEL_PROVIDER_RATE_LIMITED":
        detail["http_status"] = 429
    elif type(status) is int and 400 <= status <= 599:
        detail["http_status"] = status
    return {
        "status": "system_error",
        "rag_run_id": None,
        "answer": None,
        "citations": [],
        "verified": False,
        "retryable": isinstance(error, TransientProviderError),
        "warnings": [code],
        "coverage_report": {"execution_failure": detail, "performance": performance},
    }
