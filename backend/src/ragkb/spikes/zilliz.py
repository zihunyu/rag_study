"""Zilliz Cloud compatible BM25/ACL/watermark contract harness."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from ragkb.adapters.stubs import StubPermissionProjection
from ragkb.config import EnvLoadResult
from ragkb.spikes.common import result


def _tokens(text: str) -> list[str]:
    latin = re.findall(r"[a-z0-9_-]+", text.casefold())
    chinese = re.findall(r"[\u4e00-\u9fff]", text)
    return (
        latin + chinese + ["".join(chinese[index : index + 2]) for index in range(len(chinese) - 1)]
    )


@dataclass(frozen=True)
class _Record:
    record_id: str
    text: str
    acl_tokens: tuple[str, ...]
    watermark: int


def _bm25(query: str, records: list[_Record]) -> list[str]:
    query_tokens = _tokens(query)
    tokenized = [_tokens(record.text) for record in records]
    average_length = sum(map(len, tokenized)) / len(tokenized)
    scored: list[tuple[float, str]] = []
    for record, terms in zip(records, tokenized, strict=True):
        frequencies = Counter(terms)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            document_frequency = sum(token in document for document in tokenized)
            inverse = math.log(
                1 + (len(records) - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            denominator = frequency + 1.2 * (0.25 + 0.75 * len(terms) / average_length)
            score += inverse * frequency * 2.2 / denominator
        scored.append((score, record.record_id))
    return [record_id for score, record_id in sorted(scored, reverse=True) if score > 0]


def run_zilliz_spike(loaded: EnvLoadResult) -> dict[str, object]:
    settings = loaded.settings
    if settings is None:
        return result(
            "zilliz_cloud_bm25_acl_watermark",
            [{"name": "typed_env_available", "passed": False}],
            ["config/.env:typed_validation_failed"],
        )
    permission = StubPermissionProjection()
    records = [
        _Record("authorized", "设备 A 的保修期是三年", ("group:reader",), 12),
        _Record("unauthorized", "机密设备保修期十年", ("group:secret",), 12),
        _Record("noise", "员工餐厅开放时间", ("group:reader",), 12),
    ]
    visible = [
        item
        for item in records
        if permission.allowed(item.acl_tokens, ("group:reader",))
        and permission.watermark_ready(12, item.watermark)
    ]
    ranking = _bm25("设备保修期", visible)
    assertions = [
        {"name": "authorized_ranked_first", "passed": ranking[:1] == ["authorized"]},
        {"name": "unauthorized_never_visible", "passed": "unauthorized" not in ranking},
        {"name": "bm25_required", "passed": settings.zilliz_cloud_enable_bm25},
        {
            "name": "security_consistency_strong",
            "passed": settings.zilliz_cloud_security_consistency_level == "Strong",
        },
    ]
    blockers = [
        "zilliz_cloud_real_connection_not_executed",
        "zilliz_cloud_chinese_analyzer_not_measured",
        "zilliz_cloud_acl_watermark_latency_not_measured",
    ]
    if not loaded.configured["ZILLIZ_CLOUD_TOKEN"]:
        blockers.append("ZILLIZ_CLOUD_TOKEN:not_configured")
    return result(
        "zilliz_cloud_bm25_acl_watermark",
        assertions,
        blockers,
        {"harness_ranking": ranking, "real_request_sent": False},
    )
