from dataclasses import replace
from types import SimpleNamespace

import pytest
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.domain.errors import ProviderTimeout
from ragkb.domain.rag import AtomicClaim, ClaimVerdict, DraftAnswer, VerificationResult
from test_trusted_qa import _evidence, _service


@pytest.mark.parametrize(
    "outcome",
    ["pass", "scope_failure", "conflict", "timeout", "unchanged", "bad_cite", "no_adapter"],
)
def test_reference_policy_repair_keeps_full_conflict_pool_and_revalidates_citations(
    tmp_path, outcome
):
    sources = (
        _evidence(text="乙流程仅支持人工审核，不支持自动审批。"),
        replace(_evidence(text="甲流程只在白天支持自动审批。"), evidence_id="E2"),
    )
    claims = (AtomicClaim(sources[0].text, ("E1",)), AtomicClaim(sources[1].text, ("E2",)))
    original = DraftAnswer(
        claims[0].text + "[E1]\n\n" + claims[1].text + "[E2]",
        ("E1", "E2"),
        claims,
        synthesized=True,
    )
    final = DraftAnswer(claims[0].text + "[E1]", ("E1",), claims[:1], synthesized=True)
    initial = VerificationResult(
        (
            *[ClaimVerdict(c.text, c.evidence_ids, "SUPPORTED", "matched") for c in claims],
            ClaimVerdict(original.text, (), "INSUFFICIENT", "ANSWER_UNRELATED_BACKGROUND"),
        ),
        "fixture",
        answer_claims_covered=False,
    )
    calls = []

    def generate(question, evidence):
        calls.append("generate")
        return original

    def repair(question, previous, evidence):
        calls.append("repair")
        assert previous == original and tuple(evidence) == sources
        if outcome == "unchanged":
            return original
        if outcome == "bad_cite":
            return replace(final, citation_ids=("E404",))
        return final

    generator = SimpleNamespace(revision="fixture", generate=generate)
    if outcome != "no_adapter":
        generator.repair_relevance = repair

    class Verifier:
        count = 0

        def verify(self, question, current, evidence):
            self.count += 1
            # Removing A from the answer never removes it from conflict review.
            assert tuple(evidence) == sources
            if self.count == 1:
                return initial
            assert current == final
            if outcome == "timeout":
                raise ProviderTimeout("MODEL_PROVIDER_TIMEOUT")
            return VerificationResult(
                tuple(
                    ClaimVerdict(
                        c.text,
                        c.evidence_ids,
                        "INSUFFICIENT" if outcome == "scope_failure" else "SUPPORTED",
                        "checked",
                    )
                    for c in final.claims
                ),
                "fixture",
                conflicting_evidence_ids=("E1", "E2") if outcome == "conflict" else (),
            )

    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider(sources), generator=generator)
    service.verifier = Verifier()
    result = service.ask("乙流程也可以像甲流程一样自动审批吗？", "tenant", "user")
    assert calls == (["generate"] if outcome == "no_adapter" else ["generate", "repair"])
    assert service.verifier.count == (
        1 if outcome in {"unchanged", "bad_cite", "no_adapter"} else 2
    )
    if outcome == "pass":
        assert result.answer == final.text and result.verified
        assert {c.evidence_id for c in result.citations} == {"E1"}
    else:
        assert result.answer is None and not result.citations


@pytest.mark.parametrize("outcome", ["pass", "still_wrong", "conflict", "unchanged", "bad_cite"])
def test_surface_scope_failure_reaches_repair_with_exact_feedback_before_full_recheck(
    tmp_path, outcome
):
    sources = (
        _evidence(text="所有申请均须有效凭证。甲类可在线申请；乙类须现场申请。"),
        replace(_evidence(text="另一独立政策仍应保留在冲突核验池。"), evidence_id="E2"),
    )
    original = DraftAnswer(
        "甲类在线申请并提供有效凭证，乙类现场申请。[E1]",
        ("E1",),
        (
            AtomicClaim("甲类在线申请并提供有效凭证。", ("E1",)),
            AtomicClaim("乙类现场申请。", ("E1",)),
        ),
        synthesized=True,
    )
    final = DraftAnswer(
        "所有申请均须有效凭证；甲类在线申请，乙类现场申请。[E1]",
        ("E1",),
        (
            AtomicClaim("所有申请均须有效凭证。", ("E1",)),
            AtomicClaim("甲类在线申请，乙类现场申请。", ("E1",)),
        ),
        synthesized=True,
    )
    initial = VerificationResult(
        tuple(
            [ClaimVerdict(c.text, c.evidence_ids, "SUPPORTED", "source") for c in original.claims]
            + [ClaimVerdict(original.text, (), "INSUFFICIENT", "SHARED_SCOPE_MISSING")]
        ),
        "fixture",
        answer_claims_covered=False,
    )
    events = []

    def repair(question, previous, evidence, feedback):
        events.append("repair")
        assert previous == original and tuple(evidence) == sources and feedback == initial
        if outcome == "unchanged":
            return original
        return replace(final, citation_ids=("E404",)) if outcome == "bad_cite" else final

    generator = SimpleNamespace(
        revision="fixture", generate=lambda *args: original, repair_surface=repair
    )

    class Verifier:
        def verify(self, question, draft, evidence):
            events.append("verify")
            assert tuple(evidence) == sources
            if len(events) == 1:
                return initial
            assert draft == final
            return VerificationResult(
                tuple(
                    ClaimVerdict(
                        c.text,
                        c.evidence_ids,
                        "INSUFFICIENT" if outcome == "still_wrong" else "SUPPORTED",
                        "checked",
                    )
                    for c in draft.claims
                ),
                "fixture",
                conflicting_evidence_ids=("E1", "E2") if outcome == "conflict" else (),
            )

    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider(sources), generator=generator)
    service.verifier = Verifier()
    result = service.ask("甲乙两类申请有什么要求？", "tenant", "user")
    assert events == (
        ["verify", "repair"]
        if outcome in {"unchanged", "bad_cite"}
        else ["verify", "repair", "verify"]
    )
    if outcome == "pass":
        assert result.answer == final.text and result.verified
    else:
        assert result.answer is None and not result.citations
