import pytest
from ragkb.domain.acceptance import AcceptanceCase, mechanical_checks


def checks_for(row, *, verified=True, question="功率和额定电压是多少？ What is the voltage?"):
    case = AcceptanceCase(
        key="partial", question=question, check_citation_structure=True
    ).model_dump(mode="json")
    result = {
        "status": "answered",
        "verified": verified,
        "answer": "功率为30 W。[E1]\n\n| 项目 | 数值 |\n|---|---|\n" + row,
        "citations": [{"evidence_id": "E1"}],
    }
    return {
        c["name"]: c
        for c in mechanical_checks(
            case, result, [{"evidence_id": "E1", "document_id": "d"}], {"d"}
        )
    }


@pytest.mark.parametrize(
    "row",
    [
        "| 额定电压 | 提供的资料中未给出。 |",
        "| **额定电压** | **资料未提供** |",
        "| 额定电压 | 未说明 |",
        "| voltage | Not specified in the supplied documents. |",
    ],
)
def test_absence_only_requested_field_does_not_require_fabricated_citation(row):
    checks = checks_for(row)
    assert all(c["passed"] for c in checks.values())
    assert "1 行仅说明" in checks["表格数据行引用齐全"]["detail"]


@pytest.mark.parametrize(
    "row",
    [
        "| 额定电压 | 220 V，资料未给出 |",
        "| 额定电压 | 未提供 | 220 V |",
        "| 额定电压 | 不适用 |",
        "| 额定电压 | 不支持 |",
        "| 额定电压 | 没有额定电压 |",
        "| 额定电压 | 未提供；默认220 V |",
        "| 电压是220 V | 未提供 |",
        "| 未问的其他规则 | 未提供 |",
        "| 额定电压 | — |",
        "| 额定电压 | |",
    ],
)
def test_uncited_fact_policy_or_ambiguous_row_is_not_exempted(row):
    assert not checks_for(row)["表格数据行引用齐全"]["passed"]


def test_unverified_answer_cannot_use_absence_exception():
    checks = checks_for("| 额定电压 | 提供的资料中未给出。 |", verified=False)
    assert not checks["答案发布状态"]["passed"]
    assert not checks["表格数据行引用齐全"]["passed"]


def test_cited_facts_keep_original_citation_requirement():
    assert checks_for("| 额定电压 | 220 V [E1] | ")["表格数据行引用齐全"]["passed"]
    checks = checks_for("| 额定电压 | 220 V [E999] |")
    assert not checks["正文引用对应已发布来源"]["passed"]
