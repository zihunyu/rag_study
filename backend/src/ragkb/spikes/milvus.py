"""In-memory BM25/ACL/watermark semantics plus real Milvus blockers."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from ragkb.adapters.stubs import StubPermissionProjection
from ragkb.config.models import LoadedConfiguration
from ragkb.spikes.common import is_stubbed, result, value_at


def _tokens(text: str) -> list[str]:
    latin = re.findall(r"[a-z0-9_-]+", text.casefold())
    chinese = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = ["".join(chinese[index : index + 2]) for index in range(len(chinese) - 1)]
    return latin + chinese + bigrams


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
            document_frequency = sum(token in document for document in tokenized)
            inverse_document_frequency = math.log(
                1 + (len(records) - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            frequency = frequencies[token]
            denominator = frequency + 1.2 * (1 - 0.75 + 0.75 * len(terms) / average_length)
            score += inverse_document_frequency * frequency * 2.2 / denominator
        scored.append((score, record.record_id))
    return [record_id for score, record_id in sorted(scored, reverse=True) if score > 0]


def run_milvus_spike(loaded: LoadedConfiguration) -> dict[str, object]:
    permission = StubPermissionProjection()
    records = [
        _Record("authorized", "设备 A 的保修期是三年", ("group:reader",), 12),
        _Record("unauthorized", "机密设备的保修期是十年", ("group:secret",), 12),
        _Record("noise", "员工餐厅开放时间", ("group:reader",), 12),
    ]
    visible = [
        item
        for item in records
        if permission.allowed(item.acl_tokens, ("group:reader",))
        and permission.watermark_ready(active_watermark=12, observed_watermark=item.watermark)
    ]
    ranking = _bm25("设备保修期", visible)
    stale_visible = [
        item.record_id
        for item in records
        if permission.allowed(item.acl_tokens, ("group:reader",))
        and permission.watermark_ready(active_watermark=13, observed_watermark=item.watermark)
    ]
    assertions = [
        {"name": "authorized_exact_term_ranked_first", "passed": ranking[:1] == ["authorized"]},
        {"name": "unauthorized_id_never_visible", "passed": "unauthorized" not in ranking},
        {"name": "stale_security_watermark_fails_closed", "passed": stale_visible == []},
    ]
    blockers: list[str] = []
    for path in (
        "infrastructure.milvus.provision_mode",
        "infrastructure.milvus.uri",
        "adr_approvals.milvus_native_bm25_first",
        "adr_approvals.application_layer_rrf",
    ):
        if is_stubbed(loaded.stubbed_paths, path):
            blockers.append(path)
    if value_at(loaded.user, "adr_approvals.milvus_native_bm25_first") != "approve":
        blockers.append("ADR-007_not_approved")
    blockers.extend(
        [
            "real_milvus_2_6_chinese_analyzer_not_executed",
            "real_ARRAY_ACL_capacity_and_latency_not_measured",
            "real_consistency_watermark_not_measured",
        ]
    )
    return result(
        "milvus_bm25_acl_watermark",
        assertions,
        blockers,
        {"stub_ranking": ranking, "stub_record_count": len(records)},
    )
