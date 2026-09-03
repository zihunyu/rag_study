"""Official Precision API and OpenAI-compatible transports; runners enforce approval."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urljoin, urlparse

import httpx

from ragkb.config import EnvSettings
from ragkb.contracts.provider_execution import ProviderExecutionError


def _safe_provider_scalar(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and re.fullmatch(r"[A-Za-z0-9_.:/-]+", value)
    ):
        return value
    return None


class _ErrorEvidence(TypedDict, total=False):
    provider_error_code: str
    provider_error_type: str
    trace_id_hash: str


def _error_evidence(loaded: object) -> _ErrorEvidence:
    if not isinstance(loaded, Mapping):
        return {}
    nested = loaded.get("error")
    error = nested if isinstance(nested, Mapping) else {}
    provider_code = _safe_provider_scalar(loaded.get("code")) or _safe_provider_scalar(
        error.get("code")
    )
    provider_type = _safe_provider_scalar(loaded.get("type")) or _safe_provider_scalar(
        error.get("type")
    )
    trace_id = loaded.get("trace_id", loaded.get("traceId"))
    evidence: _ErrorEvidence = {}
    if provider_code is not None:
        evidence["provider_error_code"] = provider_code
    if provider_type is not None:
        evidence["provider_error_type"] = provider_type
    if isinstance(trace_id, (str, int)) and not isinstance(trace_id, bool):
        evidence["trace_id_hash"] = hashlib.sha256(
            str(trace_id).encode(), usedforsecurity=False
        ).hexdigest()
    return evidence


def _official_response(response: httpx.Response, code: str) -> Mapping[str, Any]:
    try:
        loaded: object = response.json()
    except ValueError:
        loaded = None
    evidence = _error_evidence(loaded)
    if response.status_code == 429:
        raise ProviderExecutionError(
            f"{code}_RATE_LIMITED",
            status_code=429,
            outcome_unknown=False,
            **evidence,
        )
    if response.status_code >= 500:
        raise ProviderExecutionError(
            f"{code}_SERVER_ERROR",
            status_code=response.status_code,
            outcome_unknown=True,
            **evidence,
        )
    if response.status_code >= 400:
        raise ProviderExecutionError(
            f"{code}_HTTP_ERROR",
            status_code=response.status_code,
            outcome_unknown=False,
            **evidence,
        )
    if loaded is None:
        raise ProviderExecutionError(f"{code}_JSON_INVALID", outcome_unknown=True)
    if not isinstance(loaded, Mapping) or loaded.get("code") != 0:
        raise ProviderExecutionError(
            f"{code}_BODY_CODE_INVALID",
            outcome_unknown=False,
            **evidence,
        )
    data = loaded.get("data")
    if not isinstance(data, Mapping):
        raise ProviderExecutionError(f"{code}_DATA_INVALID", outcome_unknown=True)
    return data


class MinerUHttpTransport:
    """MinerU Precision API batch flow from the official API contract."""

    real_network = True

    def __init__(
        self,
        base_url: str,
        *,
        allowed_signed_host_suffixes: Sequence[str] = (),
        max_download_bytes: int = 100 * 1024 * 1024,
        max_redirects: int = 2,
    ) -> None:
        parsed_base = urlparse(base_url)
        if (
            parsed_base.scheme.casefold() != "https"
            or not parsed_base.hostname
            or parsed_base.username is not None
            or parsed_base.password is not None
            or parsed_base.query
            or parsed_base.fragment
        ):
            raise ValueError("MINERU_BASE_URL_MUST_BE_HTTPS")
        self._base_url = base_url.rstrip("/")
        self._allowed_suffixes = tuple(
            value.casefold().lstrip(".") for value in allowed_signed_host_suffixes
        )
        self._max_download_bytes = max_download_bytes
        self._max_redirects = max_redirects

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _signed_url_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        if (
            parsed.scheme.casefold() != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or host == "localhost"
            or host.endswith((".localhost", ".local", ".internal", ".lan"))
        ):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host:
                return False
        else:
            if not address.is_global:
                return False
        return not self._allowed_suffixes or any(
            host == suffix or host.endswith(f".{suffix}") for suffix in self._allowed_suffixes
        )

    def create_batch(
        self,
        token: str,
        anonymous_name: str,
        data_id: str,
        is_ocr: bool,
        page_ranges: str | None,
        model_version: str,
        enable_table: bool,
        enable_formula: bool,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        file_item: dict[str, object] = {
            "name": anonymous_name,
            "data_id": data_id,
            "is_ocr": is_ocr,
        }
        if page_ranges:
            file_item["page_ranges"] = page_ranges
        try:
            response = httpx.post(
                f"{self._base_url}/file-urls/batch",
                headers=self._headers(token),
                json={
                    "files": [file_item],
                    "model_version": model_version,
                    "enable_table": enable_table,
                    "enable_formula": enable_formula,
                },
                timeout=timeout_seconds,
            )
        except (httpx.TimeoutException, TimeoutError) as error:
            raise ProviderExecutionError(
                "MINERU_CREATE_BATCH_TIMEOUT", outcome_unknown=True
            ) from error
        except httpx.RequestError as error:
            raise ProviderExecutionError(
                "MINERU_CREATE_BATCH_TRANSPORT_FAILURE", outcome_unknown=True
            ) from error
        data = _official_response(response, "MINERU_CREATE_BATCH")
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls")
        if (
            not isinstance(batch_id, str)
            or not batch_id
            or not isinstance(file_urls, Sequence)
            or isinstance(file_urls, (str, bytes))
            or len(file_urls) != 1
            or not isinstance(file_urls[0], str)
            or not self._signed_url_allowed(file_urls[0])
        ):
            raise ProviderExecutionError("MINERU_CREATE_BATCH_SCHEMA_INVALID", outcome_unknown=True)
        return {"batch_id": batch_id, "file_url": file_urls[0]}

    def put_signed(self, file_url: str, source: Path, timeout_seconds: float) -> None:
        if not self._signed_url_allowed(file_url):
            raise ProviderExecutionError("MINERU_SIGNED_UPLOAD_URL_FORBIDDEN")
        try:
            with source.open("rb") as handle:
                response = httpx.put(
                    file_url,
                    content=handle,
                    headers={},
                    timeout=timeout_seconds,
                    follow_redirects=False,
                )
        except (httpx.TimeoutException, TimeoutError) as error:
            raise ProviderExecutionError(
                "MINERU_SIGNED_UPLOAD_TIMEOUT", outcome_unknown=True
            ) from error
        except httpx.RequestError as error:
            raise ProviderExecutionError(
                "MINERU_SIGNED_UPLOAD_TRANSPORT_FAILURE", outcome_unknown=True
            ) from error
        if not 200 <= response.status_code < 300:
            raise ProviderExecutionError(
                "MINERU_SIGNED_UPLOAD_FAILED",
                status_code=response.status_code,
                outcome_unknown=response.status_code >= 500,
            )

    def batch_status(self, token: str, batch_id: str, timeout_seconds: float) -> Mapping[str, Any]:
        try:
            response = httpx.get(
                f"{self._base_url}/extract-results/batch/{batch_id}",
                headers=self._headers(token),
                timeout=timeout_seconds,
            )
        except (httpx.TimeoutException, TimeoutError) as error:
            raise ProviderExecutionError(
                "MINERU_BATCH_STATUS_TIMEOUT", outcome_unknown=True
            ) from error
        except httpx.RequestError as error:
            raise ProviderExecutionError(
                "MINERU_BATCH_STATUS_TRANSPORT_FAILURE", outcome_unknown=True
            ) from error
        return _official_response(response, "MINERU_BATCH_STATUS")

    def download_zip(self, full_zip_url: str, timeout_seconds: float) -> bytes:
        current = full_zip_url
        for redirect in range(self._max_redirects + 1):
            if not self._signed_url_allowed(current):
                raise ProviderExecutionError("MINERU_RESULT_URL_FORBIDDEN")
            try:
                with httpx.stream(
                    "GET", current, timeout=timeout_seconds, follow_redirects=False
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect >= self._max_redirects:
                            raise ProviderExecutionError("MINERU_RESULT_REDIRECT_LIMIT")
                        location = response.headers.get("location")
                        if not location:
                            raise ProviderExecutionError("MINERU_RESULT_REDIRECT_INVALID")
                        current = urljoin(current, location)
                        continue
                    if not 200 <= response.status_code < 300:
                        raise ProviderExecutionError(
                            "MINERU_RESULT_DOWNLOAD_FAILED",
                            status_code=response.status_code,
                            outcome_unknown=response.status_code >= 500,
                        )
                    declared = response.headers.get("content-length")
                    try:
                        declared_size = int(declared) if declared else 0
                    except ValueError as error:
                        raise ProviderExecutionError(
                            "MINERU_RESULT_CONTENT_LENGTH_INVALID"
                        ) from error
                    if declared_size > self._max_download_bytes:
                        raise ProviderExecutionError("MINERU_RESULT_DOWNLOAD_TOO_LARGE")
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self._max_download_bytes:
                            raise ProviderExecutionError("MINERU_RESULT_DOWNLOAD_TOO_LARGE")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except (httpx.TimeoutException, TimeoutError) as error:
                raise ProviderExecutionError(
                    "MINERU_RESULT_DOWNLOAD_TIMEOUT", outcome_unknown=True
                ) from error
            except httpx.RequestError as error:
                raise ProviderExecutionError(
                    "MINERU_RESULT_TRANSPORT_FAILURE", outcome_unknown=True
                ) from error
        raise ProviderExecutionError("MINERU_RESULT_DOWNLOAD_FAILED")


class OpenAIEmbeddingBatchTransport:
    real_network = True

    def __init__(self, settings: EnvSettings) -> None:
        self._settings = settings

    def embed(
        self,
        texts: Sequence[str],
        idempotency_key: str,
        timeout_seconds: float,
    ) -> Sequence[Sequence[float]]:
        secret = self._settings.embedding_api_key
        try:
            response = httpx.post(
                f"{self._settings.embedding_base_url.rstrip('/')}/embeddings",
                headers={
                    "Authorization": f"Bearer {secret.get_secret_value() if secret else ''}",
                    "Idempotency-Key": idempotency_key,
                },
                json={"model": self._settings.embedding_model, "input": list(texts)},
                timeout=timeout_seconds,
            )
        except (httpx.TimeoutException, TimeoutError) as error:
            raise ProviderExecutionError("EMBEDDING_TIMEOUT", outcome_unknown=True) from error
        except httpx.RequestError as error:
            raise ProviderExecutionError(
                "EMBEDDING_TRANSPORT_FAILURE", outcome_unknown=True
            ) from error
        try:
            loaded_error: object = response.json()
        except ValueError:
            loaded_error = None
        evidence = _error_evidence(loaded_error)
        if response.status_code == 429:
            raise ProviderExecutionError(
                "EMBEDDING_RATE_LIMITED",
                status_code=429,
                outcome_unknown=False,
                **evidence,
            )
        if response.status_code >= 500:
            raise ProviderExecutionError(
                "EMBEDDING_SERVER_ERROR",
                status_code=response.status_code,
                outcome_unknown=True,
                **evidence,
            )
        if response.status_code >= 400:
            raise ProviderExecutionError(
                "EMBEDDING_HTTP_ERROR",
                status_code=response.status_code,
                outcome_unknown=False,
                **evidence,
            )
        try:
            loaded = response.json()
        except ValueError as error:
            raise ProviderExecutionError(
                "EMBEDDING_RESPONSE_JSON_INVALID", outcome_unknown=False
            ) from error
        if not isinstance(loaded, Mapping):
            raise ProviderExecutionError("EMBEDDING_RESPONSE_SCHEMA_INVALID", outcome_unknown=False)
        data = loaded.get("data")
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
            raise ProviderExecutionError("EMBEDDING_RESPONSE_SCHEMA_INVALID", outcome_unknown=False)
        indexed: dict[int, Sequence[float]] = {}
        for item in data:
            if not isinstance(item, Mapping):
                raise ProviderExecutionError(
                    "EMBEDDING_RESPONSE_SCHEMA_INVALID", outcome_unknown=False
                )
            index = item.get("index")
            vector = item.get("embedding")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= len(texts)
                or index in indexed
                or not isinstance(vector, Sequence)
                or isinstance(vector, (str, bytes))
            ):
                raise ProviderExecutionError(
                    "EMBEDDING_RESPONSE_INDEX_INVALID", outcome_unknown=False
                )
            indexed[index] = vector
        if set(indexed) != set(range(len(texts))):
            raise ProviderExecutionError("EMBEDDING_RESPONSE_INDEX_INVALID", outcome_unknown=False)
        return [indexed[index] for index in range(len(texts))]


def _raise_model_http_error(response: httpx.Response, prefix: str) -> None:
    try:
        loaded: object = response.json()
    except ValueError:
        loaded = None
    evidence = _error_evidence(loaded)
    if response.status_code == 429:
        raise ProviderExecutionError(
            f"{prefix}_RATE_LIMITED",
            status_code=429,
            outcome_unknown=False,
            **evidence,
        )
    if response.status_code >= 500:
        raise ProviderExecutionError(
            f"{prefix}_SERVER_ERROR",
            status_code=response.status_code,
            outcome_unknown=True,
            **evidence,
        )
    if response.status_code >= 400:
        raise ProviderExecutionError(
            f"{prefix}_HTTP_ERROR",
            status_code=response.status_code,
            outcome_unknown=False,
            **evidence,
        )


class UatRerankerHttpTransport:
    real_network = True

    def __init__(self, settings: EnvSettings) -> None:
        self._settings = settings

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: int,
        idempotency_key: str,
        timeout_seconds: float,
    ) -> Sequence[int]:
        key = self._settings.reranker_api_key
        try:
            response = httpx.post(
                f"{self._settings.reranker_base_url.rstrip('/')}/rerank",
                headers={
                    "Authorization": f"Bearer {key.get_secret_value() if key else ''}",
                    "Idempotency-Key": idempotency_key,
                },
                json={
                    "model": self._settings.reranker_model,
                    "query": query,
                    "documents": list(documents),
                    "top_n": top_n,
                },
                timeout=timeout_seconds,
            )
        except (httpx.TimeoutException, TimeoutError) as error:
            raise ProviderExecutionError("UAT_RERANKER_TIMEOUT", outcome_unknown=True) from error
        except httpx.RequestError as error:
            raise ProviderExecutionError(
                "UAT_RERANKER_TRANSPORT_FAILURE", outcome_unknown=True
            ) from error
        _raise_model_http_error(response, "UAT_RERANKER")
        try:
            loaded = response.json()
        except ValueError as error:
            raise ProviderExecutionError(
                "UAT_RERANKER_JSON_INVALID", outcome_unknown=False
            ) from error
        if not isinstance(loaded, Mapping) or not isinstance(loaded.get("results"), Sequence):
            raise ProviderExecutionError(
                "UAT_RERANKER_RESPONSE_SCHEMA_INVALID", outcome_unknown=False
            )
        order: list[int] = []
        for item in loaded["results"]:
            if not isinstance(item, Mapping):
                raise ProviderExecutionError(
                    "UAT_RERANKER_RESPONSE_SCHEMA_INVALID", outcome_unknown=False
                )
            index = item.get("index")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= len(documents)
                or index in order
            ):
                raise ProviderExecutionError("UAT_RERANKER_INDEX_INVALID", outcome_unknown=False)
            order.append(index)
        if not order or len(order) > top_n:
            raise ProviderExecutionError("UAT_RERANKER_RESULT_COUNT_INVALID", outcome_unknown=False)
        return order


class UatLlmHttpTransport:
    real_network = True

    def __init__(self, settings: EnvSettings) -> None:
        self._settings = settings

    def generate(
        self,
        question: str,
        evidence: Sequence[Mapping[str, object]],
        idempotency_key: str,
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        key = self._settings.llm_api_key
        prompt_payload = {
            "question": question,
            "evidence": [dict(item) for item in evidence],
            "required_output": {
                "status": "answered|insufficient_evidence|needs_clarification|conflicting_evidence",
                "answer": "string",
                "citation_ids": ["bundle evidence IDs only"],
            },
        }
        try:
            response = httpx.post(
                f"{self._settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key.get_secret_value() if key else ''}",
                    "Idempotency-Key": idempotency_key,
                },
                json={
                    "model": self._settings.llm_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Use only supplied evidence. Return one JSON object with status, "
                                "answer, and citation_ids. Never cite another source."
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(prompt_payload, ensure_ascii=False),
                        },
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": self._settings.llm_max_output_tokens,
                },
                timeout=timeout_seconds,
            )
        except (httpx.TimeoutException, TimeoutError) as error:
            raise ProviderExecutionError("UAT_LLM_TIMEOUT", outcome_unknown=True) from error
        except httpx.RequestError as error:
            raise ProviderExecutionError(
                "UAT_LLM_TRANSPORT_FAILURE", outcome_unknown=True
            ) from error
        _raise_model_http_error(response, "UAT_LLM")
        try:
            loaded = response.json()
        except ValueError as error:
            raise ProviderExecutionError("UAT_LLM_JSON_INVALID", outcome_unknown=False) from error
        if not isinstance(loaded, Mapping) or not isinstance(loaded.get("choices"), Sequence):
            raise ProviderExecutionError("UAT_LLM_RESPONSE_SCHEMA_INVALID", outcome_unknown=False)
        choices = loaded["choices"]
        if len(choices) != 1 or not isinstance(choices[0], Mapping):
            raise ProviderExecutionError("UAT_LLM_RESPONSE_SCHEMA_INVALID", outcome_unknown=False)
        message = choices[0].get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise ProviderExecutionError("UAT_LLM_RESPONSE_SCHEMA_INVALID", outcome_unknown=False)
        try:
            result = json.loads(message["content"])
        except json.JSONDecodeError as error:
            raise ProviderExecutionError(
                "UAT_LLM_CONTENT_JSON_INVALID", outcome_unknown=False
            ) from error
        if not isinstance(result, Mapping):
            raise ProviderExecutionError("UAT_LLM_CONTENT_SCHEMA_INVALID", outcome_unknown=False)
        return result


class UatClaimContractHttpTransport:
    """Future-only provider adapter for validated structured UAT claims."""

    real_network = True

    def __init__(self, settings: EnvSettings) -> None:
        self._settings = settings

    def generate_claims(
        self,
        contract: Mapping[str, object],
        idempotency_key: str,
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        if contract.get("revision") != "uat-claim-contract:v1":
            raise ProviderExecutionError(
                "UAT_CLAIM_CONTRACT_REVISION_INVALID", outcome_unknown=False
            )
        key = self._settings.llm_api_key
        try:
            response = httpx.post(
                f"{self._settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key.get_secret_value() if key else ''}",
                    "Idempotency-Key": idempotency_key,
                },
                json={
                    "model": self._settings.llm_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Use only supplied evidence. Return one JSON object matching "
                                "required_output. Do not return free-form answer text."
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(contract, ensure_ascii=False),
                        },
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": self._settings.llm_max_output_tokens,
                },
                timeout=timeout_seconds,
            )
        except (httpx.TimeoutException, TimeoutError) as error:
            raise ProviderExecutionError("UAT_CLAIM_TIMEOUT", outcome_unknown=True) from error
        except httpx.RequestError as error:
            raise ProviderExecutionError(
                "UAT_CLAIM_TRANSPORT_FAILURE", outcome_unknown=True
            ) from error
        _raise_model_http_error(response, "UAT_CLAIM")
        try:
            loaded = response.json()
            choices = loaded.get("choices") if isinstance(loaded, Mapping) else None
            message = (
                choices[0].get("message")
                if isinstance(choices, Sequence)
                and len(choices) == 1
                and isinstance(choices[0], Mapping)
                else None
            )
            content = message.get("content") if isinstance(message, Mapping) else None
            result = json.loads(content) if isinstance(content, str) else None
        except (ValueError, json.JSONDecodeError, IndexError, TypeError) as error:
            raise ProviderExecutionError(
                "UAT_CLAIM_RESPONSE_SCHEMA_INVALID", outcome_unknown=False
            ) from error
        if not isinstance(result, Mapping):
            raise ProviderExecutionError("UAT_CLAIM_RESPONSE_SCHEMA_INVALID", outcome_unknown=False)
        return result
