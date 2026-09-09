from dataclasses import replace

import pytest
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.domain.answer_conditions import answer_witness_spans
from ragkb.domain.citation_repair import (
    append_missing_text_conditions,
    rebuild_from_supported_claims,
    repairable_surface,
)
from ragkb.domain.errors import ProviderTimeout
from ragkb.domain.rag import AtomicClaim, ClaimVerdict, DraftAnswer, VerificationResult
from test_trusted_qa import _evidence, _service


def sample():
    draft = DraftAnswer(
        "服务适用于已登记用户。[E1]",
        ("E1", "E2"),
        (
            AtomicClaim("服务仅适用于已登记且凭证有效的用户。", ("E1",)),
            AtomicClaim("凭证失效时不能提供服务。", ("E2",)),
        ),
        synthesized=True,
    )
    verdicts = tuple(
        ClaimVerdict(c.text, c.evidence_ids, "SUPPORTED", "source_match") for c in draft.claims
    )
    checked = VerificationResult(
        (*verdicts, ClaimVerdict(draft.text, (), "INSUFFICIENT", "SCOPE_OVERSTATED")),
        "fixture",
        citation_ids_valid=False,
        answer_claims_covered=False,
    )
    return draft, checked


def test_joint_source_conditions_remain_selectable_as_one_answer_witness():
    # Q07's repair retained both facts but put each half of a joint rule in
    # separate paragraphs, so the verifier could not cite one complete witness.
    claims = (
        AtomicClaim("办理需要购买凭证。", ("E1",)),
        AtomicClaim("办理需要设备序列号。", ("E1",)),
        AtomicClaim("另一服务仅在工作日办理。", ("E2",)),
    )
    rebuilt = rebuild_from_supported_claims(
        DraftAnswer("办理需要购买凭证。", ("E1", "E2"), claims, synthesized=True)
    )
    witnesses = tuple(answer_witness_spans(rebuilt.text).values())
    assert any(all(c.text in paragraph for c in claims[:2]) for paragraph in witnesses)
    assert rebuilt.claims == claims
    assert all(c.text in rebuilt.text for c in claims)


@pytest.mark.parametrize("failure", ["claim", "conflict", "unchecked", "extra_verdict"])
def test_rebuild_never_approves_or_repairs_an_unverified_claim(failure):
    draft, checked = sample()
    checked = {
        "claim": replace(
            checked,
            verdicts=(replace(checked.verdicts[0], verdict="INSUFFICIENT"), *checked.verdicts[1:]),
        ),
        "conflict": replace(checked, conflicting_evidence_ids=("E1", "E2")),
        "unchecked": replace(checked, conflict_checked=False),
        "extra_verdict": replace(
            checked,
            verdicts=(
                *checked.verdicts,
                ClaimVerdict("另一个事实", ("E1",), "INSUFFICIENT", "bad"),
            ),
        ),
    }[failure]
    assert not repairable_surface(draft, checked)


@pytest.mark.parametrize("outcome", ["pass", "scope_failure", "conflict", "timeout"])
def test_all_original_facts_and_uncited_conflict_context_are_rechecked(tmp_path, outcome):
    draft, checked = sample()
    assert repairable_surface(draft, checked)
    rebuilt = rebuild_from_supported_claims(draft)
    assert rebuilt.claims == draft.claims and rebuilt.citation_ids == draft.citation_ids
    assert all(claim.text in rebuilt.text for claim in draft.claims)
    sources = (
        _evidence(text=draft.claims[0].text),
        replace(_evidence(), evidence_id="E2", text=draft.claims[1].text),
        replace(
            _evidence(), evidence_id="E3", text="其他范围的规则", source_role="conflict_context"
        ),
    )

    class Generator:
        revision = "fixture"

        def generate(self, question, evidence):
            return draft

    class Verifier:
        revision = "fixture"
        calls = 0

        def verify(self, question, current, evidence):
            self.calls += 1
            assert [(e.evidence_id, e.text) for e in evidence] == [
                (e.evidence_id, e.text) for e in sources
            ]
            if self.calls == 1:
                return checked
            assert current == rebuilt
            if outcome == "timeout":
                raise ProviderTimeout("MODEL_PROVIDER_TIMEOUT")
            if outcome == "scope_failure":
                return replace(
                    checked,
                    verdicts=(
                        *checked.verdicts[:-1],
                        replace(checked.verdicts[-1], claim_text=current.text),
                    ),
                )
            return VerificationResult(
                checked.verdicts[:-1],
                "fixture",
                conflicting_evidence_ids=("E1", "E3") if outcome == "conflict" else (),
            )

    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider(sources), generator=Generator())
    service.verifier = Verifier()
    result = service.ask("服务适用条件是什么？", "tenant", "user")
    assert service.verifier.calls == 2
    if outcome == "pass":
        assert result.verified and result.answer == rebuilt.text
    else:
        assert result.answer is None and not result.citations
        if outcome == "conflict":
            assert result.status.value == "conflicting_evidence"
        else:
            assert not result.verified


@pytest.mark.parametrize("invalid", ["", "quote", "visual", "unverified", "unauthorized"])
def test_condition_completion_preserves_existing_answer_and_requires_exact_sources(invalid):
    draft, checked = sample()
    source = replace(_evidence(), evidence_id="E3", text="设备进水造成的损坏不能免费维修。")
    missing = {
        "id": "K1",
        "evidence_id": "E3",
        "source_quote": source.text,
        "status": "missing",
        "answer_quote": "",
        "reason": "An applicable exception is absent.",
    }
    checked = replace(
        checked,
        citation_ids_valid=True,
        answer_claims_covered=True,
        verdicts=(
            *checked.verdicts[:-1],
            ClaimVerdict(source.text, ("E3",), "INSUFFICIENT", "ANSWER_KEY_CONDITION_MISSING"),
        ),
        condition_checks=(missing,),
    )
    if invalid == "quote":
        missing["source_quote"] = "设备进水也能免费维修。"
    elif invalid == "visual":
        source = replace(source, locator={"visual_facts": [{"fact_id": "V1"}]})
    elif invalid == "unverified":
        checked = replace(
            checked,
            verdicts=(replace(checked.verdicts[0], verdict="INSUFFICIENT"), *checked.verdicts[1:]),
        )
    elif invalid == "unauthorized":
        source = replace(source, authorized=False)
    repaired = append_missing_text_conditions(draft, checked, (source,))
    if invalid:
        assert repaired is None
    else:
        assert repaired is not None and repaired.text.startswith(draft.text)
        assert repaired.claims[: len(draft.claims)] == draft.claims
        assert repaired.claims[-1] == AtomicClaim(source.text, ("E3",))
        assert source.text + " [E3]" in repaired.text
