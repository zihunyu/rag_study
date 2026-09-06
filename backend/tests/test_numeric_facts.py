from __future__ import annotations

from decimal import Decimal

import pytest
from ragkb.domain.numeric_facts import (
    check_numeric_facts,
    extract_numeric_facts,
    normalize_numeric_text,
    parse_number,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("十五", "15"),
        ("二十", "20"),
        ("一百零五", "105"),
        ("二〇二六", "2026"),
        ("两千零二十六", "2026"),
        ("一万零二", "10002"),
        ("一亿二千万", "120000000"),
        ("壹佰伍拾", "150"),
        ("壹萬零貳", "10002"),
        ("三点一四", "3.14"),
        ("负十五", "-15"),
        ("一点五万", "15000"),
        ("1.5万", "15000"),
        ("1万亿", "1000000000000"),
        ("１，５００．５０", "1500.50"),
        ("-0.125", "-0.125"),
        ("零", "0"),
    ],
)
def test_parse_exact_cardinals(text: str, expected: str) -> None:
    assert parse_number(text) == Decimal(expected)


@pytest.mark.parametrize(
    "text", ["一百二", "一万二", "一亿二", "两三", "十百", "一百百", "1,00", "一半", "1" * 65]
)
def test_ambiguous_or_invalid_numbers_are_not_guessed(text: str) -> None:
    with pytest.raises(ValueError):
        parse_number(text)


def test_compatibility_normalization_does_not_replace_individual_digits() -> None:
    assert normalize_numeric_text("十五天 二十天 一百零五元") == "15天20天105元"


@pytest.mark.parametrize(
    ("claim", "source"),
    [
        ("退款期限为十五天。", "退款期限为15天。"),
        ("退款期限为二十天。", "退款期限为20天。"),
        ("退款期限为15天。", "退款期限为十五天。"),
        ("保修期为三年。", "保修期为3年。"),
        ("The warranty is three years.", "The warranty is 3 years."),
        ("退款期限为两天。", "退款期限为48小时。"),
        ("运费为一百零五元。", "运费为105元。"),
        ("费用为人民币一万五千元。", "费用为15,000元。"),
        ("费用为一千五百美元。", "费用为USD 1500。"),
        ("利率为百分之十五。", "利率为15%。"),
        ("利率为百分之三点五。", "利率为3.5%。"),
        ("利率上升五个百分点。", "利率上升5个百分点。"),
        ("生效日期为二〇二六年九月十五日。", "生效日期为2026-09-15。"),
        ("生效日期为2026/9/15。", "生效日期为2026年9月15日。"),
        ("温度为负十五。", "温度为-15。"),
        ("退款期限最多十五天。", "退款期限不超过15天。"),
        ("退款期限为十五天以内。", "退款期限至多15天。"),
        ("金额至少十五元。", "金额不低于15元。"),
        ("金额上限为十五元。", "金额≤15元。"),
        ("金额下限为十五元。", "金额≥15元。"),
        ("金额超过十五元。", "金额大于15元。"),
        ("金额不足十五元。", "金额少于15元。"),
        ("退款期限为三至五天。", "退款期限为3天到5天。"),
        ("退款期限为3-5天。", "退款期限为三至五天。"),
        ("金额为100至200元。", "金额为一百元至二百元。"),
        ("服务时间为09:00至17:00。", "服务时间为9:00-17:00。"),
        ("活动为2026-09-01至2026-09-15。", "活动为2026年9月1日到2026年9月15日。"),
        ("退款期限为15天，保修期为20年。", "退款期限为十五天，保修期为二十年。"),
        ("产品使用统一标准。", "产品使用统一标准。"),
        ("重量为十五kg。", "重量为15000g。"),
        ("距离为1.5km。", "距离为1500米。"),
        ("费用为USD1500。", "费用为一千五百美元。"),
    ],
)
def test_structured_equivalence(claim: str, source: str) -> None:
    assert check_numeric_facts(claim, (source,)) == "supported", (
        extract_numeric_facts(claim),
        extract_numeric_facts(source),
    )


@pytest.mark.parametrize(
    ("claim", "source"),
    [
        ("退款期限为2天。", "退款期限为12天。"),
        ("退款期限为十五天。", "退款期限为105天。"),
        ("金额为2元。", "金额为12元。"),
        ("金额为0.5元。", "金额为10.5元。"),
        ("金额为15美元。", "金额为15元。"),
        ("利率为5%。", "利率为15%。"),
        ("利率上升5%。", "利率上升5个百分点。"),
        ("生效日期为2026-09-05。", "生效日期为2026-09-15。"),
        ("退款期限为15天。", "发布日期为2026-09-15。"),
        ("保修期为15年。", "保修期为15个月。"),
        ("退款期限为15天。", "退款期限为15个工作日。"),
        ("金额为15元。", "金额不超过15元。"),
        ("金额小于15元。", "金额不超过15元。"),
        ("金额超过15元。", "金额至少15元。"),
        ("退款期限为3天。", "退款期限为3至5天。"),
        ("退款期限为3至5天。", "退款期限为3至6天。"),
        ("服务时间为09:00至17:00。", "服务时间为09:00至19:00。"),
        ("A退款期限为20天。", "A退款期限为15天，B退款期限为20天。"),
        ("退款期限为20天。", "退款期限为15天，保修期为20天。"),
    ],
)
def test_conflicting_values_units_relations_ranges_and_objects(claim: str, source: str) -> None:
    assert check_numeric_facts(claim, (source,)) == "mismatch"


@pytest.mark.parametrize(
    ("claim", "source"),
    [
        ("费用为一百二元。", "费用为120元。"),
        ("费用为120元。", "费用为一百二元。"),
        ("费用约为15元。", "费用为15元。"),
        ("费用为15至20元（不含20元）。", "费用为15至20元。"),
        ("退款期限为三到五天。", "退款期限为五到三天。"),
        ("退款期限为半个月。", "退款期限为15天。"),
        ("服务有效期为15天。", "保修期为15天。"),
        ("生效日期为2026-02-30。", "生效日期为2026-02-28。"),
    ],
)
def test_uncertain_language_requires_semantic_review(claim: str, source: str) -> None:
    assert check_numeric_facts(claim, (source,)) == "uncertain"


def test_range_cannot_be_assembled_from_separate_evidence() -> None:
    assert check_numeric_facts("金额为15至20元。", ("金额为15元。", "金额为20元。")) == "mismatch"


def test_subject_is_retained_for_multiple_numbers() -> None:
    facts = extract_numeric_facts("A退款期限为15天，B退款期限为20天。").facts
    assert [fact.subject for fact in facts] == [("a退款期限", ""), ("b退款期限", "")]


def test_conflicting_evidence_is_not_resolved_by_first_matching_value() -> None:
    assert check_numeric_facts("金额为15元。", ("金额为15元。", "金额为20元。")) == "mismatch"


@pytest.mark.parametrize(
    "claim",
    [
        "服务时间为上午九点到下午五点。",
        "数量五。",
        "金额为1至2万元。",
        "生效日期为2026.5年9月15日。",
    ],
)
def test_unsupported_grammar_is_not_silently_accepted(claim: str) -> None:
    assert check_numeric_facts(claim, ("没有给出数值。",)) == "uncertain"


def test_uncertain_fact_does_not_hide_a_known_conflict_in_either_direction() -> None:
    assert (
        check_numeric_facts("退款期限为2天，费用约15元。", ("退款期限为12天，费用约15元。",))
        == "mismatch"
    )


def test_punctuation_in_object_identifiers_is_preserved() -> None:
    assert check_numeric_facts("A-B退款期限为15天。", ("AB退款期限为15天。",)) == "uncertain"


def test_numeric_fact_does_not_reuse_date_suffix() -> None:
    assert check_numeric_facts("日期为2026-09-15。", ("日期为12026-09-15。",)) != "supported"


def test_bare_numeric_answer_requires_a_single_unambiguous_fact() -> None:
    assert check_numeric_facts("三年", ("保修期3年",)) == "supported"
    assert check_numeric_facts("三年", ("A保修期3年，B保修期5年",)) == "uncertain"


def test_model_numbers_are_compared_as_whole_identifiers() -> None:
    source = "ThinkPad P16 Gen 3 21FA 的保修期为3年。"
    assert check_numeric_facts("ThinkPad P16 Gen 3 21FA 的保修期为三年。", (source,)) == "supported"
    assert check_numeric_facts("ThinkPad P1 Gen 3 21FA 的保修期为3年。", (source,)) == "mismatch"
    assert check_numeric_facts("ThinkPad P16 Gen 2 21FA 的保修期为3年。", (source,)) == "mismatch"


def test_generic_property_requires_one_explicit_owner() -> None:
    assert check_numeric_facts("设备保修期为三年。", ("21FA的保修期为3年。",)) == "supported"
    assert (
        check_numeric_facts("设备保修期为三年。", ("21FA的保修期为3年。", "P16的保修期为5年。"))
        == "uncertain"
    )


def test_large_date_component_does_not_overflow_the_verifier() -> None:
    assert extract_numeric_facts("日期为" + "9" * 60 + "年9月15日。").uncertain
