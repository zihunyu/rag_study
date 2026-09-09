from dataclasses import replace

import pytest
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.domain.rag import ClaimVerdict, VerificationResult
from ragkb.domain.source_lists import numbered_items, source_list_plan
from test_trusted_qa import _evidence, _service


def source(text, **kwargs):
    return _evidence(
        text=text, display_text=text, locator={"section_path": "设备检查步骤", "page": 1}, **kwargs
    )


def long_list():
    return source("\n".join(f"({i}) 检查部件{i}的完整状态。" for i in range(1, 11)))


def test_longer_numbered_scope_wins_without_losing_more_detailed_repeated_items():
    primary = long_list()
    detailed = source("(4) 检查部件4的完整状态。异常时不得启动。", evidence_id="E2")
    short = source("\n".join(primary.text.splitlines()[:4]), evidence_id="E3")
    plan = source_list_plan("设备的检查步骤是什么？", (primary, detailed, short))
    assert plan and len(plan.draft.claims) == 10
    assert plan.draft.claims[3].text.endswith("异常时不得启动。")
    assert plan.draft.claims[3].evidence_ids == ("E1", "E2", "E3")
    assert [n for n, _ in numbered_items(plan.draft.text)] == list(range(1, 11))
    assert plan.preserves_items(plan.draft)
    for i in range(1, 11):
        assert f"检查部件{i}的完整状态。" in plan.draft.text
    assert "(4)" not in plan.draft.text


@pytest.mark.parametrize(
    "question",
    [
        "第4条检查步骤是什么？",
        "简要介绍设备检查步骤",
        "设备检查步骤和收费有哪些？",
        "设备检查步骤中异常怎么处理？",
        "其他检查步骤是什么？",
    ],
)
def test_specific_or_different_questions_do_not_expand_to_the_whole_list(question):
    assert source_list_plan(question, (long_list(),)) is None


@pytest.mark.parametrize(
    "kind", ["gap", "different_document", "divergent", "unauthorized", "old_version"]
)
def test_ambiguous_or_unavailable_source_lists_do_not_elect_a_winner(kind):
    primary = long_list()
    sources = {
        "gap": (source("1. 检查甲。\n2. 检查乙。\n4. 检查丙。"),),
        "different_document": (
            primary,
            source(primary.text, evidence_id="E2", document_id="other"),
        ),
        "divergent": (primary, source("1. 不检查部件1。\n2. 不检查部件2。", evidence_id="E2")),
        "unauthorized": (replace(primary, authorized=False),),
        "old_version": (replace(primary, current_version=False),),
    }[kind]
    assert source_list_plan("设备检查步骤", sources) is None


def test_decimal_values_are_not_list_markers_and_mixed_markers_are_normalized():
    assert numbered_items("1.5 毫米\n2.5 毫米") == ()
    plan = source_list_plan("设备检查步骤", (source("(1) 检查甲。\n2 检查乙。\n3.检查丙。"),))
    assert plan and [n for n, _ in numbered_items(plan.draft.text)] == [1, 2, 3]


def test_same_scope_duplicate_with_different_number_adds_citation_without_extra_item():
    original = source("1. 设备甲仅在冷却后检查。\n2. 设备乙不得通电检查。")
    duplicate = source("14. 设备甲仅在冷却后检查。", evidence_id="E2")
    unrelated = replace(duplicate, evidence_id="E3", locator={"section_path": "另一设备步骤"})
    plan = source_list_plan("设备检查步骤", (original, duplicate, unrelated))
    assert plan and len(plan.draft.claims) == 2
    assert plan.draft.claims[0].evidence_ids == ("E1", "E2")
    assert "14." not in plan.draft.text and "[E3]" not in plan.draft.text


@pytest.mark.parametrize("change", ["-1.5", "15", "≤1.5", "1.50"])
def test_numeric_differences_never_merge_as_typographic_variations(change):
    original = source("1. 设备甲的阈值为1.5。\n2. 完成检查。")
    variant = source(f"1. 设备甲的阈值为{change}。", evidence_id="E2")
    assert source_list_plan("设备检查步骤", (original, variant)) is None


@pytest.mark.parametrize("change", ["drop", "reorder", "rewrite"])
def test_later_repairs_cannot_silently_reduce_or_change_the_source_list(change):
    plan = source_list_plan("设备检查步骤", (long_list(),))
    assert plan
    intro, *paragraphs = plan.draft.text.split("\n\n")
    if change == "drop":
        paragraphs = paragraphs[:4]
    elif change == "reorder":
        paragraphs[0], paragraphs[1] = paragraphs[1], paragraphs[0]
    else:
        paragraphs[9] = "10. 直接跳过末项检查。 [E1]"
    assert not plan.preserves_items(replace(plan.draft, text="\n\n".join([intro, *paragraphs])))


@pytest.mark.parametrize("conflict", [False, True])
def test_exact_list_still_crosses_full_verification_and_preserves_uncited_conflict_pool(
    tmp_path, conflict
):
    original = long_list()
    other = _evidence(
        evidence_id="E2",
        document_id="other",
        text="不同来源的规则。",
        locator={"section_path": "其他范围"},
        source_role="conflict_context",
    )

    class Generator:
        revision = "never"

        def generate(self, *args):
            pytest.fail("exact source list should not require a model to recreate its items")

    class Verifier:
        revision = "fixture"
        calls = 0

        def verify(self, question, draft, evidence):
            self.calls += 1
            assert len(draft.claims) == 10
            assert tuple(e.evidence_id for e in evidence) == ("E1", "E2")
            return VerificationResult(
                tuple(
                    ClaimVerdict(c.text, c.evidence_ids, "SUPPORTED", "fixture")
                    for c in draft.claims
                ),
                self.revision,
                conflicting_evidence_ids=("E1", "E2") if conflict else (),
            )

    service, _, _ = _service(
        tmp_path, SyntheticEvidenceProvider((original, other)), generator=Generator()
    )
    service.verifier = Verifier()
    result = service.ask("设备检查步骤", "tenant", "user")
    assert service.verifier.calls == 1
    if conflict:
        assert result.status.value == "conflicting_evidence" and result.answer is None
    else:
        assert result.verified and len(numbered_items(result.answer)) == 10
        assert result.coverage_report["source_list"]["complete"]


@pytest.mark.parametrize("change", ["none", "unattributed", "incomplete", "different_scope"])
def test_source_projection_requires_attribution_matching_scope_and_all_entries(tmp_path, change):
    from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
    from test_batched_verifier import ReviewTransport
    from test_model_http_adapters import _settings

    question = "设备检查步骤"
    sources = (long_list(),)
    plan = source_list_plan(question, sources)
    assert plan
    draft = plan.draft
    if change == "unattributed":
        draft = replace(draft, text=draft.text.split("\n\n", 1)[1])
    elif change == "incomplete":
        draft = replace(draft, text=draft.text.rsplit("\n\n", 1)[0])
    elif change == "different_scope":
        question = "设备检查步骤中第4项是什么？"

    # Even exact attributed source lists must retain a returned factual rejection.
    def reject(data, response, kwargs):
        response["verdicts"][0]["verdict"] = "INSUFFICIENT"
        response["verdicts"][0]["reason_code"] = "UNSUPPORTED_ENTITY_BINDING"

    transport = ReviewTransport(reject)
    settings, _ = _settings(tmp_path)
    checked = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        question, draft, sources
    )
    assert ("source_list_projection" in transport.calls[0]) == (change == "none")
    assert len(transport.calls[0]["claims"]) == 10
    assert checked.verdicts[0].verdict == "INSUFFICIENT"
    assert not checked.supported


def test_cached_answer_cannot_pass_when_source_list_entries_are_missing(tmp_path):
    sources = (long_list(),)
    plan = source_list_plan("设备检查步骤", sources)
    assert plan

    class Cache:
        def get(self, package):
            return replace(plan.draft, text=plan.draft.text.rsplit("\n\n", 1)[0])

    class Verifier:
        revision = "fixture"

        def verify(self, question, draft, evidence):
            return VerificationResult(
                tuple(
                    ClaimVerdict(c.text, c.evidence_ids, "SUPPORTED", "fixture")
                    for c in draft.claims
                ),
                self.revision,
            )

    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider(sources))
    service.cache = Cache()
    service.verifier = Verifier()
    result = service.ask("设备检查步骤", "tenant", "user")
    assert result.answer is None and not result.verified
    assert result.warnings == ("SOURCE_LIST_COVERAGE_INCOMPLETE",)
