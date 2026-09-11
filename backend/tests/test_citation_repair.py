import json
from dataclasses import replace

import pytest
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.domain.citation_repair import (
    apply_citation_additions,
    repairable_citations,
    validate_citation_only_change,
)
from ragkb.domain.errors import ProviderTimeout
from ragkb.domain.rag import AtomicClaim, ClaimVerdict, DraftAnswer, VerificationResult
from test_generation_outcomes import generator_for
from test_trusted_qa import _evidence, _service


def draft():
    return DraftAnswer(
        "两类事项如下：\n\n| 事项 | 内容 |\n|---|---|\n"
        "| 设备 | 保修三年 [E1] |\n| 合同 | 仅限城区 [E2] |",
        ("E1", "E2"),
        (AtomicClaim("设备保修三年。", ("E1",)), AtomicClaim("服务仅限城区。", ("E2",))),
        synthesized=True,
    )


def verification(value, **kwargs):
    return VerificationResult(
        tuple(ClaimVerdict(c.text, c.evidence_ids, "SUPPORTED", "supported") for c in value.claims),
        "fixture",
        **kwargs,
    )


def test_patch_preserves_all_facts_table_and_existing_sources():
    before = draft()
    after = apply_citation_additions(before, [{"line_id": "L1", "claim_ids": ["C1", "C2"]}])
    validate_citation_only_change(before, after)
    assert after.text == before.text.replace("两类事项如下：", "两类事项如下： [E1][E2]")
    assert after.claims is before.claims
    with pytest.raises(ValueError, match="CHANGED_FACTS"):
        validate_citation_only_change(
            before, replace(after, text=after.text.replace("三年", "十年"))
        )


@pytest.mark.parametrize("padding", [" ", "  ", "\t", "\t  "])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_citation_insertion_preserves_original_table_cell_whitespace(padding, newline):
    original = DraftAnswer(
        newline.join([
            "| 项目 | 数值 |", "|---|---|",
            f"| 功率 | 480 W{padding}|", "| 容量 | 960 Wh | [E1][E2]",
        ]),
        ("E1", "E2"),
        (AtomicClaim("功率为480 W。", ("E1",)), AtomicClaim("容量为960 Wh。", ("E2",))),
        synthesized=True,
    )
    patched = apply_citation_additions(original, [{"line_id": "L3", "claim_ids": ["C1"]}])
    validate_citation_only_change(original, patched)
    assert f"480 W{padding} [E1]|" in patched.text
    assert patched.claims == original.claims
    for changed in ("481 W", "480 kW", "480 V"):
        with pytest.raises(ValueError, match="CHANGED_FACTS"):
            validate_citation_only_change(
                original, replace(patched, text=patched.text.replace("480 W", changed))
            )


def test_rebuilding_list_keeps_appended_conditions_outside_numbering():
    from ragkb.domain.citation_repair import rebuild_from_supported_claims

    original = DraftAnswer(
        "",
        ("E1", "E2"),
        (
            AtomicClaim("检查第一项。", ("E1",)),
            AtomicClaim("检查第二项。", ("E1",)),
            AtomicClaim("操作前必须断电。", ("E2",)),
        ),
        synthesized=True,
    )
    rebuilt = rebuild_from_supported_claims(original, numbered=True, list_claim_count=2)
    assert "1. 检查第一项。" in rebuilt.text and "2. 检查第二项。" in rebuilt.text
    assert "3." not in rebuilt.text
    assert "相关限制：\n\n操作前必须断电。 [E2]" in rebuilt.text
    assert rebuilt.claims == original.claims and rebuilt.citation_ids == original.citation_ids
    with pytest.raises(ValueError, match="INVALID_LIST_CLAIM_COUNT"):
        rebuild_from_supported_claims(original, numbered=True, list_claim_count=4)


def test_partially_cited_line_gets_missing_support_without_changing_existing_citations():
    before = DraftAnswer(
        "按每人每晚计算，单位为元/晚。[E1]",
        ("E1", "E2"),
        (AtomicClaim("按每人每晚计算。", ("E1",)), AtomicClaim("单位为元/晚。", ("E2",))),
        synthesized=True,
    )
    after = apply_citation_additions(before, [{"line_id": "L1", "claim_ids": ["C1", "C2"]}])
    validate_citation_only_change(before, after)
    assert after.text == before.text + " [E2]"
    assert after.claims is before.claims
    with pytest.raises(ValueError, match="CHANGED_SOURCES"):
        validate_citation_only_change(before, replace(after, text=after.text.replace("[E1]", "")))


@pytest.mark.parametrize(
    "additions",
    [
        [{"line_id": "L1", "claim_ids": ["C9"]}],
        [
            {"line_id": "L5", "claim_ids": ["C1"]}
        ],  # Repeating an existing marker does not add missing support.
        [{"line_id": "L1", "claim_ids": ["C1"], "text": "invented"}],
        [{"line_id": "L1", "claim_ids": ["C1"]}] * 2,
        [{"line_id": [], "claim_ids": ["C1"]}],
    ],
)
def test_unknown_or_ambiguous_edits_are_rejected(additions):
    with pytest.raises(ValueError, match="CITATION_REPAIR_INVALID"):
        apply_citation_additions(draft(), additions)


@pytest.mark.parametrize("failure", ["conflict", "unsupported", "extra_fact", "unchecked"])
def test_repair_gate_cannot_override_factual_or_conflict_failures(failure):
    value = draft()
    checked = verification(value, citation_ids_valid=False)
    checked = {
        "conflict": replace(checked, conflicting_evidence_ids=("E1", "E2")),
        "unsupported": replace(
            checked,
            verdicts=(replace(checked.verdicts[0], verdict="INSUFFICIENT"), *checked.verdicts[1:]),
        ),
        "extra_fact": replace(checked, answer_claims_covered=False),
        "unchecked": replace(checked, conflict_checked=False),
    }[failure]
    assert not repairable_citations(value, checked)


def test_citation_planner_uses_only_supplied_claims_and_does_not_rewrite(tmp_path):
    generator, transport = generator_for(
        tmp_path, {"additions": [{"line_id": "L1", "claim_ids": ["C1", "C2"]}]}
    )
    sources = (
        _evidence(text="设备保修三年。"),
        replace(_evidence(), evidence_id="E2", text="服务仅限城区。"),
    )
    patched = generator.repair_citations("介绍条款", draft(), sources)
    validate_citation_only_change(draft(), patched)
    sent = json.loads(transport.calls[0]["payload"]["messages"][-1]["content"])
    assert [s["evidence_id"] for s in sent["sources"]] == ["E1", "E2"]
    assert "[E1][E2]" in patched.text.splitlines()[0]


def test_condition_batch_receives_actual_complementary_cited_source(tmp_path):
    from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
    from ragkb.domain.answer_conditions import condition_requirements
    from test_model_http_adapters import _MockTransport, _settings

    sources = (
        _evidence(text="目录第一部分：条目一、条目二。"),
        replace(_evidence(), evidence_id="E2", text="目录第二部分：条目三。此页仅包含第二部分。"),
    )
    value = DraftAnswer(
        "目录包含条目一、条目二 [E1]，以及条目三 [E2]。",
        ("E1", "E2"),
        (AtomicClaim("条目一、条目二。", ("E1",)), AtomicClaim("条目三。", ("E2",))),
        synthesized=True,
    )
    required = condition_requirements(sources)
    transport = _MockTransport(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "condition_checks": [
                                    {
                                        "id": r["id"],
                                        "status": "not_applicable",
                                        "answer_quote": "",
                                        "reason": "The cited first part supplies missing entries.",
                                    }
                                    for r in required
                                ]
                            }
                        )
                    }
                }
            ]
        }
    )
    settings, _ = _settings(tmp_path)
    verifier = OpenAICompatibleClaimVerifier(settings, transport=transport)
    verifier._verify_condition_batch("目录有哪些条目？", value, sources, required, required)
    sent = json.loads(transport.calls[0]["payload"]["messages"][-1]["content"])
    assert {s["evidence_id"]: s["text"] for s in sent["sources"]} == {
        s.evidence_id: s.text for s in sources
    }


@pytest.mark.parametrize(
    "recheck", ["pass", "conflict", "bad_citation", "timeout", "condition_missing"]
)
def test_full_reverification_is_required_and_citation_repair_is_bounded(tmp_path, recheck):
    value = draft()
    sources = (
        _evidence(text="设备保修三年。"),
        replace(
            _evidence(), evidence_id="E2", text="服务仅限城区。", source_role="conflict_context"
        ),
    )

    class Generator:
        revision = "fixture"
        repairs = 0

        def generate(self, question, evidence):
            return value

        def repair_citations(self, question, current, evidence):
            self.repairs += 1
            return apply_citation_additions(current, [{"line_id": "L1", "claim_ids": ["C1", "C2"]}])

    class Verifier:
        revision = "fixture"
        calls = 0

        def verify(self, question, current, evidence):
            self.calls += 1
            assert [(e.evidence_id, e.text) for e in evidence] == [
                (e.evidence_id, e.text) for e in sources
            ]  # Full source text and uncited conflict context survive both repairs.
            if self.calls == 1:
                return verification(current, citation_ids_valid=False)
            if recheck == "timeout":
                raise ProviderTimeout("MODEL_PROVIDER_TIMEOUT")
            if recheck == "condition_missing":
                return verification(
                    current,
                    condition_checks=(
                        {
                            "id": "K1",
                            "evidence_id": "E2",
                            "source_quote": "服务仅限城区。",
                            "status": "missing",
                            "answer_quote": "",
                            "reason": "missing",
                        },
                    ),
                )
            return verification(
                current,
                citation_ids_valid=recheck != "bad_citation",
                conflicting_evidence_ids=("E1", "E2") if recheck == "conflict" else (),
            )

    # The initial draft already cites both sources; give both to generation too.
    sources = (
        sources[0],
        replace(sources[1], source_role="hit"),
        replace(
            _evidence(), evidence_id="E3", text="外区维修另行付费。", source_role="conflict_context"
        ),
    )
    generator = Generator()
    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider(sources), generator=generator)
    service.verifier = Verifier()
    result = service.ask("介绍条款", "tenant", "user")
    assert generator.repairs == 1
    assert service.verifier.calls >= 2
    if recheck == "pass":
        assert result.verified and result.answer
    else:
        assert result.answer is None
        assert result.status.value == (
            "conflicting_evidence"
            if recheck == "conflict"
            else "insufficient_evidence"
            if recheck == "condition_missing"
            else "system_error"
        )


@pytest.mark.parametrize("recheck", ["pass", "conflict", "bad_citation", "timeout"])
@pytest.mark.parametrize("source_list", [False, True])
def test_empty_citation_patch_rebuilds_supported_claims_and_rechecks_every_gate(
    tmp_path, recheck, source_list
):
    value = replace(draft(), text="以下是全部规则：\n保修三年。 [E1]\n服务仅限城区。 [E2]")
    sources = (
        replace(_evidence(text="设备保修三年。"), locator={"section_path": "服务规则"}),
        replace(_evidence(), evidence_id="E2", text="服务仅限城区。"),
        replace(
            _evidence(), evidence_id="E3", text="外区另行付费。", source_role="conflict_context"
        ),
    )

    class Generator:
        revision = "fixture"
        repairs = 0

        def generate(self, question, evidence):
            return value

        def repair_citations(self, question, current, evidence):
            self.repairs += 1
            return current

    class Verifier:
        revision = "fixture"
        calls = 0

        def verify(self, question, current, evidence):
            self.calls += 1
            assert evidence == sources
            if self.calls == 1:
                return verification(current, citation_ids_valid=False)
            assert current.claims == value.claims and current.citation_ids == value.citation_ids
            assert "全部规则" not in current.text
            assert all(c.text in current.text for c in value.claims)
            if source_list:
                assert current.text.startswith("1. ") and "\n\n2. " in current.text
            if recheck == "timeout":
                raise ProviderTimeout("MODEL_PROVIDER_TIMEOUT")
            return verification(
                current,
                citation_ids_valid=recheck != "bad_citation",
                conflicting_evidence_ids=("E1", "E3") if recheck == "conflict" else (),
            )

    generator = Generator()
    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider(sources), generator=generator)
    service.verifier = Verifier()
    result = service.ask("完整列出服务规则" if source_list else "介绍规则", "tenant", "user")
    assert generator.repairs == (0 if source_list else 1) and service.verifier.calls == 2
    if recheck == "pass":
        assert result.verified and result.answer
    else:
        assert result.answer is None and not result.citations
