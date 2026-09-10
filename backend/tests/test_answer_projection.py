import json
from dataclasses import replace

import pytest
from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.domain.answer_projection import (
    approved_projection,
    duplicate_table_candidate,
    verified_projection,
)
from ragkb.domain.rag import AtomicClaim, DraftAnswer
from test_citation_repair import verification
from test_model_http_adapters import _MockTransport, _settings
from test_trusted_qa import _evidence, _service


def sample():
    paragraph = "甲设备功率为480 W，容量为960 Wh。[E1]"
    value = DraftAnswer(
        paragraph + "\n\n| 项目 | 值 |\n|---|---|\n| 功率 | 480 W [E1] |\n| 容量 | 960 Wh [E1] |",
        ("E1",),
        (AtomicClaim("甲设备功率为480 W，容量为960 Wh。", ("E1",)),),
        synthesized=True,
    )
    return paragraph, value


def receipt(**kwargs):
    return {
        "all_content_preserved": True,
        "citations_valid": True,
        "retained_claim_ids": ["C1"],
        **kwargs,
    }


def test_candidate_is_not_approval_and_keeps_the_exact_cited_paragraph():
    paragraph, value = sample()
    assert duplicate_table_candidate("介绍参数", value) == paragraph
    assert approved_projection("介绍参数", value, None) == ""
    assert approved_projection("介绍参数", value, receipt()) == paragraph
    assert approved_projection("介绍参数", value, receipt(retained_claim_ids=[])) == ""
    assert approved_projection("介绍参数", value, receipt(all_content_preserved="true")) == ""
    assert approved_projection("介绍参数", value, receipt(citations_valid=False)) == ""


@pytest.mark.parametrize("changed", ["number", "source", "extra_paragraph", "table_requested"])
def test_table_with_additional_content_or_requested_format_is_not_a_candidate(changed):
    _, value = sample()
    question = "介绍参数"
    if changed == "number":
        value = replace(value, text=value.text + "\n| 电压 | 220 V [E1] |")
    elif changed == "source":
        value = replace(value, text=value.text.replace("480 W [E1] |", "480 W [E2] |"))
    elif changed == "extra_paragraph":
        value = replace(value, text=value.text + "\n\n仅限已登记用户。[E1]")
    else:
        question = "请用表格介绍参数"
    assert duplicate_table_candidate(question, value) == ""


@pytest.mark.parametrize("failure", ["conflict", "claim", "citation", "condition", "rewrite"])
def test_projection_never_overrides_a_failed_check_or_drops_a_condition_witness(failure):
    paragraph, value = sample()
    checked = verification(value, answer_projection=paragraph)
    if failure == "conflict":
        checked = replace(checked, conflicting_evidence_ids=("E1", "E2"))
    elif failure == "claim":
        checked = replace(checked, verdicts=(replace(checked.verdicts[0], verdict="INSUFFICIENT"),))
    elif failure == "citation":
        checked = replace(checked, citation_ids_valid=False)
    elif failure == "condition":
        checked = replace(
            checked,
            condition_checks=({"status": "covered", "answer_quote": "| 功率 | 480 W [E1] |"},),
        )
    else:
        checked = replace(checked, answer_projection=paragraph.replace("480", "490"))
    assert verified_projection("介绍参数", value, checked) == ""


def test_retained_real_condition_witness_is_preserved():
    paragraph, value = sample()
    checked = verification(
        value,
        answer_projection=paragraph,
        condition_checks=({"status": "covered", "answer_quote": paragraph},),
    )
    assert verified_projection("介绍参数", value, checked) == paragraph


@pytest.mark.parametrize("approved", [True, False])
def test_optional_projection_is_reviewed_in_the_existing_full_pool_call(tmp_path, approved):
    paragraph, value = sample()
    response = {
        "verdicts": [{"claim_id": "C1", "verdict": "SUPPORTED", "reason_code": "supported"}],
        "answer_check": {"covered": True, "citations_valid": True, "reason_code": "supported"},
        "conflict_check": {"checked": True, "conflicting_evidence_ids": []},
        "projection_check": receipt(all_content_preserved=approved),
    }
    transport = _MockTransport({"choices": [{"message": {"content": json.dumps(response)}}]})
    settings, _ = _settings(tmp_path)
    verifier = OpenAICompatibleClaimVerifier(settings, transport=transport)
    sources = (_evidence(text=value.claims[0].text),)
    result = verifier.verify("介绍参数", value, sources)
    assert len(transport.calls) == 1
    payload = json.loads(transport.calls[0]["payload"]["messages"][-1]["content"])
    assert payload["duplicate_table_candidate"] == paragraph
    assert payload["answer"] == value.text and payload["conflict_evidence"]
    assert result.supported
    assert result.answer_projection == (paragraph if approved else "")


def test_service_projects_only_after_verification_without_a_second_model_call(tmp_path):
    paragraph, value = sample()

    class Generator:
        revision = "fixture"

        def generate(self, question, evidence):
            return value

    class Verifier:
        revision = "fixture"
        calls = 0

        def verify(self, question, current, evidence):
            self.calls += 1
            assert current == value
            return verification(value, answer_projection=paragraph)

    service, _, _ = _service(
        tmp_path,
        SyntheticEvidenceProvider((_evidence(text=value.claims[0].text),)),
        generator=Generator(),
    )
    service.verifier = Verifier()
    result = service.ask("介绍参数", "tenant", "user")
    assert result.verified and result.answer == paragraph
    assert service.verifier.calls == 1
    assert [c.evidence_id for c in result.citations] == ["E1"]
