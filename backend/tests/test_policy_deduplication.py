from __future__ import annotations

from dataclasses import replace

import pytest
from ragkb.adapters.rag_stubs import DeterministicBufferedGenerator
from ragkb.adapters.retrieval_memory import InMemoryHybridIndex, InMemoryRetrievalControlPlane
from ragkb.adapters.stubs import DeterministicEmbedding, DeterministicReranker
from ragkb.application.evidence import SearchBackedEvidenceProvider
from ragkb.application.qa import InMemoryVerifiedAnswerCache, verified_answer_cache_key
from ragkb.application.search import HybridSearchService, _shingles, near_duplicate
from ragkb.domain.rag import AnswerStatus
from test_hybrid_search import _candidate, _chunk, _context
from test_trusted_qa import _service

COMMON = (
    "本制度用于规范客户服务管理工作，保障业务办理过程公开透明。"
    "申请人应提交完整材料，经办人员负责登记核对并告知办理进度。"
    "受理部门按照职责分工开展审查，相关记录应妥善保存以备复核。"
    "工作人员应认真履行岗位职责，做好交接安排和信息保密工作。"
    "发现材料遗漏时应及时通知补充，处理结果通过原申请渠道送达。"
    "涉及跨部门事项应明确责任人，建立沟通记录并跟踪办理情况。"
    "监督部门负责检查执行情况，收集意见并定期完善服务流程。"
)


def test_non_numeric_case_and_word_boundaries_are_not_erased():
    assert not near_duplicate(COMMON + "Applies to US.", COMMON + "Applies to us.", threshold=0.92)
    assert not near_duplicate(COMMON + "now here", COMMON + "nowhere", threshold=0.92)
    assert not near_duplicate(
        COMMON + "退款期限为{numeric_fact}15天。",
        COMMON + "退款期限为15天{numeric_fact}。",
        threshold=0.92,
    )


def _search(chunks, *, limit=5):
    return HybridSearchService(
        DeterministicEmbedding(),
        InMemoryHybridIndex(
            bm25=tuple(_candidate(item.chunk_id, "bm25", i) for i, item in enumerate(chunks, 1)),
            security_watermark=10,
        ),
        InMemoryRetrievalControlPlane({item.chunk_id: item for item in chunks}),
        DeterministicReranker(),
        bm25_top_k=20,
        dense_top_k=20,
        rrf_k=60,
        rerank_top_k=10,
        final_evidence_count=limit,
    )


def _provider(search):
    return SearchBackedEvidenceProvider(
        search,
        space_id="space-1",
        active_generation_id="generation-1",
        active_permission_revision=lambda: 4,
        required_security_watermark=lambda: 1,
        prompt_revision="test",
        model_revision="test",
        final_evidence_count=search.final_evidence_count,
        clock=lambda: 100,
    )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("退款申请期限为15天。", "退款申请期限为30天。"),
        ("退款金额为100元。", "退款金额为100美元。"),
        ("退款金额不超过100元。", "退款金额超过100元。"),
        ("服务时间为09:00至17:00。", "服务时间为09:00至19:00。"),
        ("生效日期为2026年9月15日。", "生效日期为2026年9月30日。"),
        ("员工可以申请退款。", "员工不可以申请退款。"),
        ("适用于正式员工。", "适用于临时员工。"),
        ("上海员工补贴15元，北京员工补贴30元。", "上海员工补贴30元，北京员工补贴15元。"),
        ("退款比例为15%。", "退款比例为30%。"),
    ],
)
def test_high_similarity_never_erases_a_material_policy_difference(left, right):
    first, second = COMMON + left, COMMON + right
    a, b = _shingles(first), _shingles(second)
    assert len(a & b) / len(a | b) > 0.92
    assert not near_duplicate(first, second, threshold=0.92)
    search = _search([_chunk("a", first, "a"), _chunk("b", second, "b")])
    result = search.search("退款", _context())
    assert {hit.chunk_id for hit in result.hits} == {"a", "b"}


def test_equal_numbers_and_formatting_can_merge_with_all_sources():
    first = COMMON + "退款期限为15天。"
    second = COMMON + "退款期限为十五天。"
    assert near_duplicate(first, second, threshold=0.92)
    search = _search([_chunk("a", first, "a"), _chunk("b", second, "b")])
    result = search.search("退款", _context())
    assert len(result.hits) == 1
    assert result.hits[0].duplicate_sources[0].chunk_id == "b"
    assert {source.chunk_id for source in result.review_sources} == {"a", "b"}


def test_same_checksum_cannot_bypass_fact_comparison():
    search = _search(
        [
            _chunk("a", COMMON + "退款期限为15天。", "same-checksum"),
            _chunk("b", COMMON + "退款期限为30天。", "same-checksum"),
        ]
    )
    assert len(search.search("退款", _context()).hits) == 2


def test_same_retrieval_text_cannot_hide_different_display_text():
    a = _chunk("a", "退款期限15天", "a")
    b = replace(_chunk("b", "退款期限30天", "b"), retrieval_text=a.retrieval_text)
    assert len(_search([a, b]).search("退款", _context()).hits) == 2


def test_validity_difference_prevents_merging_and_expired_sources_are_excluded():
    a = _chunk("a", "退款期限15天", "a")
    b = replace(_chunk("b", a.retrieval_text, "b"), valid_from_epoch=1)
    expired = replace(_chunk("expired", "退款期限30天", "expired"), valid_to_epoch=1)
    result = _search([a, b, expired]).search("退款", _context())
    assert {hit.chunk_id for hit in result.hits} == {"a", "b"}
    assert {source.chunk_id for source in result.review_sources} == {"a", "b"}


def test_unauthorized_duplicate_never_appears_in_source_map_or_review_pool():
    a = _chunk("a", "退款期限15天", "same")
    hidden = _chunk("secret", a.retrieval_text, "same", visibility="RESTRICTED", acl=("secret",))
    result = _search([a, hidden]).search("退款", _context())
    assert result.hits[0].duplicate_sources == ()
    assert [source.chunk_id for source in result.review_sources] == ["a"]


def test_display_limit_does_not_remove_uncited_conflicting_policy(tmp_path):
    search = _search(
        [
            _chunk("a", COMMON + "退款申请期限为15天。", "a"),
            _chunk("b", COMMON + "退款申请期限为30天。", "b"),
        ],
        limit=1,
    )
    provider = _provider(search)
    package = provider.build_package("退款期限？", "tenant-1", "user", clearance_level=1)
    assert [e.chunk_id for e in package.generation_evidence] == ["a"]
    assert [e.chunk_id for e in package.evidence] == ["a", "b"]
    assert package.evidence[1].source_role == "conflict_context"
    assert all(e.authority_rank == 0 for e in package.evidence)
    service, _, _ = _service(
        tmp_path,
        provider,
        generator=DeterministicBufferedGenerator(answer="退款申请期限为15天。"),
    )
    result = service.ask("退款期限？", "tenant-1", "user", clearance_level=1)
    assert result.status is AnswerStatus.CONFLICTING_EVIDENCE
    assert result.answer is None and not result.citations


def test_uncited_policy_change_invalidates_cached_answer_and_triggers_conflict(tmp_path):
    search = _search(
        [_chunk("a", "退款期限为15天。", "a"), _chunk("b", "退款期限为15天。", "b")], limit=1
    )
    provider = _provider(search)
    service, _, _ = _service(
        tmp_path, provider, generator=DeterministicBufferedGenerator(answer="退款期限为15天。")
    )
    service.cache = InMemoryVerifiedAnswerCache()
    before = provider.build_package("退款期限？", "tenant-1", "user", clearance_level=1)
    assert service.ask("退款期限？", "tenant-1", "user", clearance_level=1).verified
    search.control_plane._chunks["b"] = _chunk("b", "退款期限为30天。", "b-new")
    after = provider.build_package("退款期限？", "tenant-1", "user", clearance_level=1)
    assert verified_answer_cache_key(before) != verified_answer_cache_key(after)
    result = service.ask("退款期限？", "tenant-1", "user", clearance_level=1)
    assert result.status is AnswerStatus.CONFLICTING_EVIDENCE
    assert result.answer is None


def test_merged_sources_are_persisted_and_rechecked_but_not_repeated_in_generation(tmp_path):
    search = _search(
        [
            _chunk("a", "退款期限为15天。", "a"),
            _chunk("b", "退款期限为15天。", "b"),
        ],
        limit=1,
    )
    provider = _provider(search)
    seen_generation = []
    checked = []

    class Generator(DeterministicBufferedGenerator):
        def generate(self, question, evidence):
            seen_generation.append(tuple(e.chunk_id for e in evidence))
            return super().generate(question, evidence)

    service, repository, _ = _service(
        tmp_path,
        provider,
        generator=Generator(answer="退款期限为15天。"),
    )

    class Permission:
        def recheck(self, evidence, **kwargs):
            checked.append(tuple(e.chunk_id for e in evidence))
            return True

    service.permission = Permission()
    result = service.ask("退款期限？", "tenant-1", "user", clearance_level=1)
    assert result.status is AnswerStatus.ANSWERED
    assert seen_generation == [("a",)]
    assert checked and all(set(ids) == {"a", "b"} for ids in checked)
    restored = repository.get_package(result.rag_run_id)
    assert [(e.chunk_id, e.source_role) for e in restored.evidence] == [
        ("a", "hit"),
        ("b", "conflict_context"),
    ]


def test_source_map_is_available_in_search_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from ragkb.api.app import create_app
    from test_search_backed_qa import _components_with_search_qa

    for key, value in {
        "APP_ENV": "testing",
        "RAG_RUNTIME_PROFILE": "local",
        "VECTOR_BACKEND": "local",
        "AUTH_MODE": "local_single_user",
        "REAL_PROVIDER_CALLS_ENABLED": "false",
        "EXTERNAL_LIFECYCLE_MUTATIONS_ENABLED": "false",
        "OTEL_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    components = _components_with_search_qa(tmp_path)
    original = components.search_service.control_plane._chunks["qa-chunk"]
    duplicate = replace(original, chunk_id="duplicate", locator={"page": 9})
    components.search_service.control_plane._chunks[duplicate.chunk_id] = duplicate
    components.search_service.index = InMemoryHybridIndex(
        bm25=(
            replace(
                _candidate("qa-chunk", "bm25", 1), document_version_id=original.document_version_id
            ),
            replace(
                _candidate("duplicate", "bm25", 2), document_version_id=original.document_version_id
            ),
        ),
        security_watermark=0,
    )
    response = TestClient(create_app(components)).post("/api/v1/search", json={"query": "保修期"})
    assert response.status_code == 200
    assert len(response.json()["hits"]) == 1
    source = response.json()["hits"][0]["duplicate_sources"][0]
    assert source["chunk_id"] == "duplicate"
    assert source["document_version_id"] == original.document_version_id
    assert source["locator"] == {"page": 9}


def test_review_pool_without_selected_hits_does_not_reach_generator(tmp_path):
    search = _search([_chunk("a", "退款期限15天。", "a")])

    class NoSelection:
        revision = "none"

        def rerank(self, *args):
            return ()

    search.reranker = NoSelection()
    provider = _provider(search)
    service, _, _ = _service(tmp_path, provider)

    class NeverGenerate:
        revision = "never"

        def generate(self, *args):
            pytest.fail("review-only evidence is not generation context")

    service.generator = NeverGenerate()
    result = service.ask("退款期限？", "tenant-1", "user", clearance_level=1)
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
