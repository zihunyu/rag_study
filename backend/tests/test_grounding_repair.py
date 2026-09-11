import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.domain.grounding_repair import apply_source_bindings, repairable_grounding
from ragkb.domain.rag import AtomicClaim, ClaimVerdict, DraftAnswer, VerificationResult
from test_generation_outcomes import generator_for
from test_trusted_qa import _evidence, _service


def fixture():
    sources = (
        _evidence(text="甲区 | 24"),
        replace(_evidence(text="下表金额为每人每日费用上限，单位为元。"), evidence_id="E2"),
        replace(_evidence(text="其他来源必须仍参与冲突检查。"), evidence_id="E3"),
    )
    draft = DraftAnswer(
        "| 区域 | 上限 |\n|---|---|\n| 甲区 | 24元/人/日 [E1] |",
        ("E1",),
        (AtomicClaim("甲区每日每人费用上限为24元。", ("E1",)),),
        synthesized=True,
    )
    checked = VerificationResult(
        (ClaimVerdict(draft.claims[0].text, ("E1",), "INSUFFICIENT", "UNIT_SOURCE_MISSING"),),
        "fixture",
    )
    bindings = [{"claim_id": "C1", "evidence_additions": ["E2"], "line_ids": ["L3"]}]
    return sources, draft, checked, bindings


def test_source_additions_preserve_exact_fact_text_cells_and_existing_citations():
    sources, draft, checked, bindings = fixture()
    assert repairable_grounding(draft, checked)
    result = apply_source_bindings(draft, bindings, sources)
    assert result.text == draft.text.replace(" [E1] |", " [E1]  [E2]|")
    assert result.claims == (replace(draft.claims[0], evidence_ids=("E1", "E2")),)
    assert result.citation_ids == ("E1", "E2")
    assert apply_source_bindings(draft, [], sources) == draft


def test_fact_binding_can_be_completed_when_body_already_has_shared_source():
    sources, draft, _, bindings = fixture()
    draft = replace(draft, text=draft.text.replace("[E1]", "[E1][E2]"), citation_ids=("E1", "E2"))
    result = apply_source_bindings(draft, bindings, sources)
    assert result.text == draft.text
    assert result.claims[0].evidence_ids == ("E1", "E2")


@pytest.mark.parametrize(
    "change",
    [
        "unknown_source",
        "unauthorized",
        "old_version",
        "unknown_line",
        "unknown_claim",
        "duplicate_source",
        "existing_source",
        "duplicate_binding",
        "rewrite_fact",
        "visual",
    ],
)
def test_invalid_binding_cannot_change_facts_or_add_unavailable_sources(change):
    sources, draft, _, bindings = fixture()
    if change == "unknown_source":
        bindings[0]["evidence_additions"] = ["E404"]
    if change == "unauthorized":
        sources = (sources[0], replace(sources[1], authorized=False))
    if change == "old_version":
        sources = (sources[0], replace(sources[1], current_version=False))
    if change == "unknown_line":
        bindings[0]["line_ids"] = ["L404"]
    if change == "unknown_claim":
        bindings[0]["claim_id"] = "C404"
    if change == "duplicate_source":
        bindings[0]["evidence_additions"] = ["E2", "E2"]
    if change == "existing_source":
        bindings[0]["evidence_additions"] = ["E1"]
    if change == "duplicate_binding":
        bindings *= 2
    if change == "rewrite_fact":
        bindings[0]["text"] = "改成240元"
    if change == "visual":
        draft = replace(draft, claims=(replace(draft.claims[0], visual_fact_ids=("V1",)),))
    with pytest.raises(ValueError, match="GROUNDING_REPAIR_INVALID"):
        apply_source_bindings(draft, bindings, sources)


@pytest.mark.parametrize(
    "outcome", ["pass", "unsupported", "conflict", "unchanged", "contradicted"]
)
def test_binding_repair_is_once_and_must_pass_full_original_evidence_review(tmp_path, outcome):
    sources, draft, checked, bindings = fixture()
    if outcome == "contradicted":
        checked = replace(checked, verdicts=(replace(checked.verdicts[0], verdict="CONTRADICTED"),))
        assert not repairable_grounding(draft, checked)
    events = []

    def repair(question, previous, evidence, feedback):
        events.append("repair")
        assert previous == draft and evidence == sources and feedback == checked
        return draft if outcome == "unchanged" else apply_source_bindings(draft, bindings, evidence)

    generator = SimpleNamespace(
        revision="fixture", generate=lambda *args: draft, repair_grounding=repair
    )

    class Verifier:
        def verify(self, question, current, evidence):
            events.append("verify")
            assert evidence == sources
            if len(events) == 1:
                return checked
            return VerificationResult(
                tuple(
                    ClaimVerdict(
                        c.text,
                        c.evidence_ids,
                        "INSUFFICIENT" if outcome == "unsupported" else "SUPPORTED",
                        "checked",
                    )
                    for c in current.claims
                ),
                "fixture",
                conflicting_evidence_ids=("E1", "E3") if outcome == "conflict" else (),
            )

    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider(sources), generator=generator)
    service.verifier = Verifier()
    result = service.ask("甲区的费用上限及计费单位是什么？", "tenant", "user")
    assert events == (
        ["verify"]
        if outcome == "contradicted"
        else ["verify", "repair"]
        if outcome == "unchanged"
        else ["verify", "repair", "verify"]
    )
    if outcome == "pass":
        assert (
            result.verified
            and result.answer == apply_source_bindings(draft, bindings, sources).text
        )
    else:
        assert result.answer is None and not result.citations


def test_adapter_only_accepts_source_bindings_and_exposes_full_sources_with_rejection(tmp_path):
    sources, draft, checked, bindings = fixture()
    generator, transport = generator_for(tmp_path, {"bindings": bindings})
    result = generator.repair_grounding("费用及单位？", draft, sources, checked)
    assert result == apply_source_bindings(draft, bindings, sources)
    messages = transport.calls[0]["payload"]["messages"]
    payload = json.loads(messages[1]["content"])
    assert {s["evidence_id"] for s in payload["sources"]} == {"E1", "E2", "E3"}
    assert payload["rejected"][0]["reason"] == "UNIT_SOURCE_MISSING"
    assert payload["claims"][0]["text"] == draft.claims[0].text
    assert "never change answer facts" in messages[0]["content"]
