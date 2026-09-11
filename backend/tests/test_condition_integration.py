from dataclasses import replace

import pytest
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.domain.citation_repair import append_missing_text_conditions
from ragkb.domain.errors import ProviderTimeout
from ragkb.domain.rag import AtomicClaim, ClaimVerdict, DraftAnswer, VerificationResult
from test_trusted_qa import _evidence, _service

COMMON = "办理必须身份核验、授权有效；符合条件的申请由服务中心处理。"


def sample():
    sources = (
        _evidence(text=COMMON),
        replace(
            _evidence(text="常规入口登记后5天内可提交；特别入口不适用该时限。"), evidence_id="E2"
        ),
    )
    claims = (
        AtomicClaim("常规入口登记后5天内可提交，办理必须身份核验、授权有效。", ("E1", "E2")),
        AtomicClaim("特别入口不适用登记后5天的提交时限。", ("E2",)),
        AtomicClaim("符合条件的申请由服务中心处理。", ("E1",)),
    )
    draft = DraftAnswer(
        "| 入口 | 条件 |\n|---|---|\n"
        "| 常规入口 | 登记后5天内可提交；办理必须身份核验、授权有效。[E1][E2] |\n"
        "| 特别入口 | 不适用登记后5天的提交时限。[E2] |\n\n"
        "符合条件的申请由服务中心处理。[E1]",
        ("E1", "E2"),
        claims,
        synthesized=True,
    )
    missing = {
        "id": "K1",
        "evidence_id": "E1",
        "source_quote": COMMON,
        "status": "missing",
        "answer_quote": "",
        "reason": "Shared scope was narrowed.",
    }
    checked = VerificationResult(
        (
            *[ClaimVerdict(c.text, c.evidence_ids, "SUPPORTED", "source_match") for c in claims],
            ClaimVerdict(COMMON, ("E1",), "INSUFFICIENT", "ANSWER_KEY_CONDITION_MISSING"),
        ),
        "fixture",
        condition_checks=(missing,),
    )
    return sources, draft, checked


def test_complete_shared_quote_replaces_exact_parts_without_losing_claims_or_table():
    sources, draft, checked = sample()
    repaired = append_missing_text_conditions(draft, checked, sources)
    assert repaired
    assert repaired.text.count("办理必须身份核验、授权有效") == 1
    assert repaired.text.count("符合条件的申请由服务中心处理") == 1
    assert "| 常规入口 | 登记后5天内可提交。[E1][E2] |" in repaired.text
    assert "| 特别入口 | 不适用登记后5天的提交时限。[E2] |" in repaired.text
    assert COMMON + " [E1]" in repaired.text
    assert repaired.claims == (*draft.claims, AtomicClaim(COMMON, ("E1",)))
    assert repaired.citation_ids == draft.citation_ids


@pytest.mark.parametrize(
    "prefix",
    [
        "并非办理必须身份核验、授权有效。[E1]",
        "若风险升高，办理必须身份核验、授权有效。[E1]",
        "办理必须身份核验、授权有效。[E2]",
        "1. 办理必须身份核验、授权有效。[E1]",
        "> 办理必须身份核验、授权有效。[E1]",
        "| 特别入口 | 办理必须身份核验、授权有效。[E1] |",
    ],
)
def test_repair_does_not_erase_qualifiers_other_sources_list_items_or_whole_cells(prefix):
    sources, draft, checked = sample()
    repaired = append_missing_text_conditions(replace(draft, text=prefix), checked, sources)
    assert repaired is None or repaired.text.startswith(prefix)


@pytest.mark.parametrize("mode", ["exact", "recompose"])
@pytest.mark.parametrize("outcome", ["pass", "scope_failure", "conflict", "timeout"])
def test_integrated_repair_must_pass_every_verification_gate(tmp_path, outcome, mode):
    sources, draft, checked = sample()
    if mode == "recompose":
        draft = replace(draft, text=draft.text.replace("；办理", "，且办理"))
    repaired = append_missing_text_conditions(draft, checked, sources)
    if mode == "recompose":
        assert repaired is None  # Overlap cannot authorize deleting a qualified clause.
        repaired = replace(
            draft,
            text="共同要求：" + COMMON + "[E1]\n\n常规入口登记后5天内可提交。[E2]\n\n"
            "特别入口不适用登记后5天的提交时限。[E2]",
            claims=(*draft.claims, AtomicClaim(COMMON, ("E1",))),
        )

    class Generator:
        revision = "fixture"
        calls = 0
        repairs = 0

        def generate(self, question, evidence):
            self.calls += 1
            return draft

        def repair_conditions(self, question, previous, evidence):
            self.repairs += 1
            assert previous == draft
            assert COMMON in evidence[0].locator["conditions_to_preserve"]
            assert [(e.evidence_id, e.text) for e in evidence] == [
                (e.evidence_id, e.text) for e in sources
            ]
            return repaired

    class Verifier:
        calls = 0

        def verify(self, question, current, evidence):
            self.calls += 1
            assert [(e.evidence_id, e.text) for e in evidence] == [
                (e.evidence_id, e.text) for e in sources
            ]
            if self.calls == 1:
                return checked
            assert current == repaired
            if outcome == "timeout":
                raise ProviderTimeout("MODEL_PROVIDER_TIMEOUT")
            verdicts = tuple(
                ClaimVerdict(
                    c.text,
                    c.evidence_ids,
                    "INSUFFICIENT" if outcome == "scope_failure" else "SUPPORTED",
                    "rechecked",
                )
                for c in current.claims
            )
            return VerificationResult(
                verdicts,
                "fixture",
                conflicting_evidence_ids=("E1", "E2") if outcome == "conflict" else (),
            )

    generator, verifier = Generator(), Verifier()
    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider(sources), generator=generator)
    service.verifier = verifier
    result = service.ask("两种入口的条件及符合条件时的处理方是什么？", "tenant", "user")
    assert generator.calls == 1 and verifier.calls == 2
    # Even an exact append candidate must not precede a second scope repair when
    # the generator can reconcile the complete answer in one repair pass.
    assert generator.repairs == 1
    if outcome == "pass":
        assert result.verified and result.answer == repaired.text
    else:
        assert result.answer is None and not result.citations
        if outcome == "conflict":
            assert result.status.value == "conflicting_evidence"
        else:
            assert not result.verified
