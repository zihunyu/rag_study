from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from ragkb.adapters.provider_http import (
    MinerUHttpTransport,
    OpenAIEmbeddingBatchTransport,
    UatLlmHttpTransport,
    UatRerankerHttpTransport,
)
from ragkb.config import EnvSettings
from ragkb.contracts.provider_execution import ProviderExecutionError


def test_precision_batch_paths_and_signed_put_follow_official_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_post(url, **kwargs):
        captured["post_url"] = url
        captured["post_headers"] = kwargs["headers"]
        captured["post_json"] = kwargs["json"]
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "ok",
                "data": {
                    "batch_id": "batch-id",
                    "file_urls": ["https://upload.storage.example/signed"],
                },
            },
        )

    def fake_put(url, **kwargs):
        captured["put_url"] = url
        captured["put_headers"] = kwargs["headers"]
        return httpx.Response(200)

    def fake_get(url, **kwargs):
        captured["get_url"] = url
        captured["get_headers"] = kwargs["headers"]
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "ok",
                "data": {
                    "batch_id": "batch-id",
                    "extract_result": [
                        {
                            "data_id": "anonymous-id",
                            "state": "done",
                            "full_zip_url": "https://download.storage.example/result.zip",
                        }
                    ],
                },
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "put", fake_put)
    monkeypatch.setattr(httpx, "get", fake_get)
    source = tmp_path / "private.png"
    source.write_bytes(b"authorized")
    transport = MinerUHttpTransport("https://mineru.net/api/v4")

    created = transport.create_batch(
        "secret-token",
        "sample-anonymous.png",
        "anonymous-id",
        True,
        None,
        "vlm",
        True,
        True,
        30,
    )
    transport.put_signed(str(created["file_url"]), source, 30)
    status = transport.batch_status("secret-token", str(created["batch_id"]), 30)

    assert str(captured["post_url"]).endswith("/file-urls/batch")
    assert captured["post_json"] == {
        "files": [{"name": "sample-anonymous.png", "data_id": "anonymous-id", "is_ocr": True}],
        "model_version": "vlm",
        "enable_table": True,
        "enable_formula": True,
    }
    assert captured["put_url"] == "https://upload.storage.example/signed"
    assert captured["put_headers"] == {}
    assert str(captured["get_url"]).endswith("/extract-results/batch/batch-id")
    assert status["extract_result"][0]["state"] == "done"


def test_precision_body_code_and_signed_host_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(
            200, json={"code": 17, "msg": "provider failure", "data": {}}
        ),
    )
    transport = MinerUHttpTransport(
        "https://mineru.net/api/v4", allowed_signed_host_suffixes=["storage.example"]
    )
    with pytest.raises(ProviderExecutionError, match="BODY_CODE") as raised:
        transport.create_batch(
            "secret-token", "sample.png", "anonymous", True, None, "vlm", True, True, 30
        )
    assert raised.value.provider_error_code == "17"
    assert raised.value.outcome_unknown is False
    with pytest.raises(ProviderExecutionError, match="FORBIDDEN"):
        transport.put_signed("http://127.0.0.1/private", Path("unused"), 30)


def test_precision_complete_4xx_keeps_only_safe_error_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(
            401,
            json={
                "code": "A0202",
                "msg": "sensitive provider message must not persist",
                "trace_id": "opaque-trace-value",
            },
        ),
    )
    transport = MinerUHttpTransport("https://mineru.net/api/v4")
    with pytest.raises(ProviderExecutionError) as raised:
        transport.create_batch(
            "secret-token", "sample.png", "anonymous", True, None, "vlm", True, True, 30
        )
    error = raised.value
    assert error.status_code == 401
    assert error.provider_error_code == "A0202"
    assert error.trace_id_hash is not None and len(error.trace_id_hash) == 64
    assert error.outcome_unknown is False
    assert "sensitive provider message" not in str(error)


def test_precision_base_url_must_be_https() -> None:
    with pytest.raises(ValueError, match="MUST_BE_HTTPS"):
        MinerUHttpTransport(
            "http://mineru.net/api/v4", allowed_signed_host_suffixes=["storage.example"]
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/signed",
        "https://10.0.0.1/signed",
        "https://localhost/signed",
        "https://user:password@storage.example/signed",
        "http://storage.example/signed",
    ],
)
def test_default_signed_url_policy_rejects_private_or_credentialed_targets(
    url: str,
) -> None:
    transport = MinerUHttpTransport("https://mineru.net/api/v4")
    with pytest.raises(ProviderExecutionError, match="FORBIDDEN"):
        transport.put_signed(url, Path("never-read"), 30)


def test_embedding_response_is_sorted_by_unique_complete_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = EnvSettings(
        embedding_base_url="https://embedding.example/v1",
        embedding_api_key=SecretStr("test-secret"),
        embedding_model="test-model",
    )
    responses = [
        httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [2.0]},
                    {"index": 0, "embedding": [1.0]},
                ]
            },
        ),
        httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [1.0]},
                    {"index": 0, "embedding": [2.0]},
                ]
            },
        ),
    ]
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: responses.pop(0))
    transport = OpenAIEmbeddingBatchTransport(settings)

    assert transport.embed(["first", "second"], "safe-idempotency", 30) == [
        [1.0],
        [2.0],
    ]
    with pytest.raises(ProviderExecutionError, match="INDEX_INVALID"):
        transport.embed(["first", "second"], "safe-idempotency", 30)


def test_embedding_complete_400_keeps_code_type_but_not_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = EnvSettings(
        embedding_base_url="https://embedding.example/v1",
        embedding_api_key=SecretStr("test-secret"),
        embedding_model="test-model",
    )
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(
            400,
            json={
                "error": {
                    "code": "InvalidParameter",
                    "type": "invalid_request_error",
                    "message": "sensitive request detail",
                },
                "trace_id": "opaque-embedding-trace",
            },
        ),
    )
    with pytest.raises(ProviderExecutionError) as raised:
        OpenAIEmbeddingBatchTransport(settings).embed(["synthetic"], "safe-key", 30)
    error = raised.value
    assert error.status_code == 400
    assert error.provider_error_code == "InvalidParameter"
    assert error.provider_error_type == "invalid_request_error"
    assert error.trace_id_hash is not None and len(error.trace_id_hash) == 64
    assert error.outcome_unknown is False
    assert "sensitive request detail" not in str(error)


def test_uat_reranker_and_llm_http_contracts_parse_strict_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = EnvSettings(
        reranker_base_url="https://reranker.example/v1",
        reranker_api_key=SecretStr("test-reranker-secret"),
        reranker_model="reranker-model",
        llm_base_url="https://llm.example/v1",
        llm_api_key=SecretStr("test-llm-secret"),
        llm_model="llm-model",
    )

    def fake_post(url, **kwargs):
        if str(url).endswith("/rerank"):
            return httpx.Response(200, json={"results": [{"index": 1}, {"index": 0}]})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"status":"answered","answer":"ok","citation_ids":["e1"]}'
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    assert UatRerankerHttpTransport(settings).rerank(
        "synthetic question", ["one", "two"], 2, "safe-key", 30
    ) == [1, 0]
    generated = UatLlmHttpTransport(settings).generate(
        "synthetic question",
        [{"evidence_id": "e1", "locator": {"page": 1}, "content": "synthetic"}],
        "safe-key",
        30,
    )
    assert generated["status"] == "answered"
    assert generated["citation_ids"] == ["e1"]


def test_uat_http_error_keeps_safe_scalars_only(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = EnvSettings(
        reranker_base_url="https://reranker.example/v1",
        reranker_api_key=SecretStr("test-secret"),
        reranker_model="reranker-model",
    )
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(
            400,
            json={
                "error": {
                    "code": "InvalidInput",
                    "type": "invalid_request_error",
                    "message": "sensitive provider detail",
                },
                "trace_id": "opaque-trace",
            },
        ),
    )
    with pytest.raises(ProviderExecutionError) as raised:
        UatRerankerHttpTransport(settings).rerank("question", ["document"], 1, "safe-key", 30)
    error = raised.value
    assert error.status_code == 400
    assert error.provider_error_code == "InvalidInput"
    assert error.provider_error_type == "invalid_request_error"
    assert error.trace_id_hash is not None and len(error.trace_id_hash) == 64
    assert "sensitive provider detail" not in str(error)
