from dataclasses import replace

import pytest
from ragkb.domain.answer_conditions import (
    ConditionCheckError,
    condition_requirements,
    validate_condition_checks,
)
from ragkb.domain.rag import DraftAnswer
from test_trusted_qa import _evidence


def sample():
    source = _evidence(text="标准设备签收后15天内可退货；定制设备不适用。")
    required = condition_requirements((source,))
    draft = DraftAnswer(
        "标准设备签收后15天内可退货。[E1]\n\n请保留包装。\n\n定制设备不适用。[E1]",
        ("E1",),
    )
    check = {
        "id": "K1",
        "status": "covered",
        "answer_quote": "",
        "reason": "Same rule and exception.",
    }
    return required, draft, check


def test_joint_witness_preserves_real_separated_rule_and_exception():
    required, draft, check = sample()
    single = validate_condition_checks([{**check, "answer_span_id": "A1"}], required, draft)
    assert single[0]["status"] == "missing"
    result = validate_condition_checks(
        [{**check, "answer_span_ids": ["A1", "A3"]}], required, draft
    )
    assert result[0]["status"] == "covered"
    assert result[0]["answer_quote"] == draft.text


@pytest.mark.parametrize("ids", [[], ["A99"], ["A1", "A1"], [False], "A1"])
def test_joint_witness_rejects_invalid_selection(ids):
    required, draft, check = sample()
    with pytest.raises(ConditionCheckError, match="VERIFIER_CONDITION_WITNESS_INVALID"):
        validate_condition_checks([{**check, "answer_span_ids": ids}], required, draft)


def test_joint_witness_does_not_cover_an_unstated_exception_or_an_uncited_source():
    required, draft, check = sample()
    result = validate_condition_checks(
        [{**check, "answer_span_ids": ["A1", "A2"]}], required, draft
    )
    assert result[0]["status"] == "missing"
    with pytest.raises(ConditionCheckError, match="VERIFIER_CONDITION_WITNESS_INVALID"):
        validate_condition_checks(
            [{**check, "answer_span_ids": ["A1", "A3"]}], required, replace(draft, citation_ids=())
        )


def test_joint_witness_cannot_be_used_for_not_applicable_or_conflicting_selections():
    required, draft, check = sample()
    for changed in (
        {"status": "not_applicable", "answer_span_ids": ["A1"]},
        {"answer_span_id": "A2", "answer_span_ids": ["A1", "A3"]},
    ):
        with pytest.raises(ConditionCheckError):
            validate_condition_checks([{**check, **changed}], required, draft)


def test_reference_to_all_conditions_uses_the_actual_conditions_as_witness():
    source = _evidence(text="与本条款配套的增值服务补充条款仅在满足其中全部条件时适用。")
    required = condition_requirements((source,))
    paragraph = "增值服务须购买套餐并在启用后30天内登记。[E1][E2]"
    draft = DraftAnswer(paragraph, ("E1", "E2"))
    check = {
        "id": "K1",
        "status": "covered",
        "answer_span_id": "A1",
        "answer_quote": "",
        "reason": "Independent review confirms the cited package and registration requirements.",
    }
    result = validate_condition_checks([check], required, draft)
    assert result[0]["status"] == "covered"
    # The local reference handling must never turn a semantic missing verdict into a pass.
    check.update(
        status="missing", answer_span_id=None, reason="The registration condition is absent."
    )
    assert validate_condition_checks([check], required, draft)[0]["status"] == "missing"


def test_concrete_restriction_beside_reference_still_requires_its_witness():
    source = _evidence(
        text="与本条款配套的增值服务补充条款仅在满足其中全部条件时适用；仅限凭证有效且登记完整的用户。"
    )
    required = condition_requirements((source,))
    paragraph = "增值服务需购买套餐并登记。[E1]"
    check = {
        "id": "K1",
        "status": "covered",
        "answer_quote": paragraph,
        "reason": "Claimed covered.",
    }
    result = validate_condition_checks([check], required, DraftAnswer(paragraph, ("E1",)))
    # An explicit missing condition from semantic review always remains missing.
    # This case also exercises the independent conjunction guard on the retained clause.
    assert result[0]["status"] == "missing"
