"""Reproduced row swaps, Chinese numeric gaps, references and answer omissions."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.application.qa import CompositeClaimVerifier, DeterministicClaimVerifier
from ragkb.document_processing.local_visual_check import check_extraction
from ragkb.domain.answer_conditions import condition_quotes, condition_requirements
from ragkb.domain.errors import InvalidProviderResponse
from ragkb.domain.rag import AnswerStatus, AtomicClaim, DraftAnswer
from ragkb.domain.source_references import references
from ragkb.domain.visual_numbers import contains_number, critical_tokens
from ragkb.domain.visuals import VisualCell, VisualExtraction, VisualTable
from ragkb.infrastructure.overview import explicit_image_links
from ragkb.infrastructure.visual_revisions import exclusion_closure
from test_model_http_adapters import _MockTransport, _settings
from test_trusted_qa import _evidence, _service
from test_workspace_redesign import publish, upload, workspace  # noqa: F401


def table_extraction(grid):
    return VisualExtraction(
        kind="table",
        title="",
        transcription="",
        description="",
        graphs=[],
        tables=[
            VisualTable(
                title="",
                rows=len(grid),
                columns=len(grid[0]),
                header_rows=1,
                cells=[
                    VisualCell(row=r, column=c, rowspan=1, colspan=1, text=value)
                    for r, row in enumerate(grid)
                    for c, value in enumerate(row)
                ],
            )
        ],
        uncertainties=[],
    )


def measured(grid):
    return {
        "engine": "independent-fixture",
        "regions": [
            {
                "id": f"r{r}c{c}",
                "text": value,
                "score": 0.99,
                "bbox": [c * 0.3 + 0.02, r * 0.15 + 0.02, c * 0.3 + 0.25, r * 0.15 + 0.1],
            }
            for r, row in enumerate(grid)
            for c, value in enumerate(row)
        ],
    }


def text_extraction(text):
    return VisualExtraction(
        kind="text",
        title="",
        transcription=text,
        description="",
        graphs=[],
        tables=[],
        uncertainties=[],
    )


@pytest.mark.parametrize(
    "grid",
    [
        [["Product", "Power"], ["A", "200 W"], ["B", "100 W"]],
        [["Power", "Product"], ["A", "100 W"], ["B", "200 W"]],
    ],
)
def test_numbers_present_in_image_do_not_confirm_swapped_rows_or_headers(grid):
    correct = [["Product", "Power"], ["A", "100 W"], ["B", "200 W"]]
    result = check_extraction(table_extraction(grid), measured(correct))
    assert result["status"] == "disagreement"
    assert result["issues"] and all(not t["region_ids"] for t in result["targets"])


def test_geometry_resolves_repeated_values_and_never_guesses_without_coordinates():
    grid = [["Product", "Power", "Voltage"], ["A", "100 W", "220 V"], ["B", "100 W", "24 V"]]
    value, reading = table_extraction(grid), measured(grid)
    check = check_extraction(value, reading)
    assert check["status"] == "consistent"
    assert next(t for t in check["targets"] if (t["row"], t["column"]) == (2, 1))["region_ids"] == [
        "r2c1"
    ]
    for region in reading["regions"]:
        region.pop("bbox")
    assert check_extraction(value, reading)["status"] == "inconclusive"


def test_merged_header_geometry_retains_spans():
    grid = [["Limits", ""], ["Product", "Power"], ["A", "100 W"]]
    value, reading = table_extraction(grid), measured(grid)
    value.tables[0].cells = [c for c in value.tables[0].cells if (c.row, c.column) != (0, 1)]
    value.tables[0].cells[0].colspan = 2
    value.tables[0].header_rows = 2
    assert check_extraction(value, reading)["status"] == "consistent"


@pytest.mark.parametrize(
    "text,token",
    [
        ("血量1037", "1037"),
        ("功率323 W", "323 W"),
        ("容量5000毫安时", "5000毫安时"),
        ("温度−20℃", "−20°C"),
        ("电压１．５kV", "1.5kV"),
        ("误差±5%", "±5%"),
        ("压力1.2MPa", "1.2MPa"),
        ("时间1e-3s", "1e-3s"),
        ("范围10–20℃", "10–20°C"),
        ("范围10-20 V", "10-20 V"),
    ],
)
def test_chinese_boundaries_units_signs_and_ranges_are_checked(text, token):
    assert critical_tokens(text) == [token]
    assert (
        check_extraction(text_extraction(text), {"engine": "fixture", "regions": []})["status"]
        == "disagreement"
    )
    assert contains_number(text, token)


@pytest.mark.parametrize(
    "actual,expected",
    [
        ("323 W", "23 W"),
        ("23 MW", "23 mW"),
        ("10 V", "10–20 V"),
        ("1.2Pa", "1.2MPa"),
        ("23.5 W", "23 W"),
    ],
)
def test_numeric_witnesses_cannot_borrow_prefixes_units_or_range_endpoints(actual, expected):
    assert not contains_number(actual, expected)


class Rows:
    def __init__(self, text):
        self.text = text

    def list_chunks(self, *args, **kwargs):
        return [{"text": self.text, "locator": {"section_path": "Other"}}]


@pytest.mark.parametrize(
    "caption,reference,dependent",
    [
        ("图1", "参见图10", False),
        ("图1", "参见图1.2", False),
        ("图1.2", "参见图1.20", False),
        ("图1-2", "参见图1-20", False),
        ("Figure 1.2", "参见图 1.2", True),
        ("图1", "参见表1", False),
        ("Table 10", "参见表10", True),
        ("图1", "参见图1。", True),
    ],
)
def test_exclusion_and_retrieval_use_complete_reference_identities(caption, reference, dependent):
    assets = {
        "a": {"id": "a", "section_path": "Original", "caption": caption, "status": "verified"}
    }
    assert ("Other" in exclusion_closure(Rows(reference), "v", assets, ["Original"])) is dependent
    assert (
        bool(explicit_image_links(list(assets.values()), [_evidence(text=reference)])) is dependent
    )


def test_invalid_reference_suffix_does_not_backtrack_to_a_shorter_number():
    assert references("Figure 1.2a") == []
    assets = {"a": {"section_path": "Product1", "caption": ""}}
    assert exclusion_closure(Rows("Product10"), "v", assets, ["Product1"]) == ["Product1"]
    assert exclusion_closure(Rows("Product1.2"), "v", assets, ["Product1"]) == ["Product1"]


SOURCE = "设备支持上门维修。仅限保修期内且位于城区的设备。进水损坏不包括在免费维修内。"
FULL = "设备支持上门维修，仅限保修期内且位于城区的设备；进水损坏不包括在免费维修内。[E1]"
SHORT = "设备支持上门维修。[E1]"


def draft(text):
    return DraftAnswer(
        text, ("E1",), (AtomicClaim(text.replace("[E1]", ""), ("E1",)),), synthesized=True
    )


class ConditionTransport(_MockTransport):
    def __init__(self, mutate=None):
        super().__init__({})
        self.mutate = mutate

    def post_json(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        sent = json.loads(kwargs["payload"]["messages"][1]["content"])
        checks = [
            {
                "id": r["id"],
                "status": "covered" if sent["answer"] == FULL else "missing",
                "answer_quote": FULL if sent["answer"] == FULL else "",
                "reason": "检查地域、保修期限和进水例外",
            }
            for r in sent["condition_requirements"]
        ]
        payload = {
            "verdicts": [
                {"claim_id": c["claim_id"], "verdict": "SUPPORTED", "reason_code": "SUPPORTED"}
                for c in sent["claims"]
            ],
            "conflict_check": {"checked": True, "conflicting_evidence_ids": []},
            "answer_check": {
                "covered": True,
                "citations_valid": True,
                "reason_code": "TRUE_CLAIMS",
            },
            "condition_checks": checks,
        }
        if self.mutate:
            self.mutate(payload)
        return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


def test_source_condition_inventory_keeps_exceptions_and_deduplicates_repeated_headers():
    evidence = _evidence(text=SOURCE, locator={"section_path": "Service"})
    assert len(condition_quotes(SOURCE)) == 2
    requirements = condition_requirements(
        (evidence, replace(evidence, evidence_id="E2", chunk_id="c2"))
    )
    assert len(requirements) == 2
    assert requirements[0]["equivalent_evidence_ids"] == ["E1", "E2"]


def test_true_answer_and_chapter_citation_do_not_pass_when_conditions_are_missing(tmp_path):
    settings, _ = _settings(tmp_path)
    model = OpenAICompatibleClaimVerifier(settings, transport=ConditionTransport())
    result = model.verify("总结维修政策", draft(SHORT), (_evidence(text=SOURCE),))
    assert not result.supported
    assert [c["status"] for c in result.condition_checks] == ["missing", "missing"]
    assert model.verify("总结维修政策", draft(FULL), (_evidence(text=SOURCE),)).supported


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.pop("condition_checks"),
        lambda p: p["condition_checks"].reverse(),
        lambda p: p["condition_checks"][0].update(answer_quote="这句话不在答案中"),
        lambda p: p["condition_checks"][0].update(
            status="not_applicable", answer_quote="", reason=""
        ),
    ],
)
def test_missing_fabricated_or_incomplete_condition_reviews_are_rejected(tmp_path, mutation):
    settings, _ = _settings(tmp_path)
    model = OpenAICompatibleClaimVerifier(settings, transport=ConditionTransport(mutation))
    with pytest.raises(InvalidProviderResponse, match="VERIFIER_CONDITION"):
        model.verify("总结维修政策", draft(FULL), (_evidence(text=SOURCE),))


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_omission_repair_is_bounded_and_verified_before_release(
    tmp_path, repair_succeeds, monkeypatch
):
    # Exercise the model fallback when exact text completion is unavailable.
    monkeypatch.setattr("ragkb.application.qa.append_missing_text_conditions", lambda *_: None)
    settings, _ = _settings(tmp_path)

    class Generator:
        revision = "omission-fixture"
        calls = 0

        def generate(self, question, evidence):
            self.calls += 1
            if self.calls == 2:
                assert len(evidence[0].locator["conditions_to_preserve"]) == 2
            return draft(FULL if repair_succeeds and self.calls == 2 else SHORT)

    generator = Generator()
    service, _, _ = _service(
        tmp_path, SyntheticEvidenceProvider((_evidence(text=SOURCE),)), generator=generator
    )
    service.verifier = CompositeClaimVerifier(
        DeterministicClaimVerifier(),
        OpenAICompatibleClaimVerifier(settings, transport=ConditionTransport()),
    )
    result = service.ask("总结维修政策", "tenant", "user")
    assert generator.calls == 2
    assert result.coverage_report["conditions"]["checked"] == 2
    if repair_succeeds:
        assert result.status == AnswerStatus.ANSWERED and result.answer == FULL and result.verified
    else:
        assert result.answer is None and not result.verified
        assert "ANSWER_KEY_CONDITION_MISSING" in result.warnings
        assert not result.coverage_report["conditions"]["complete"]


def test_a_source_condition_cannot_be_marked_covered_by_an_unrelated_answer_quote(tmp_path):
    settings, _ = _settings(tmp_path)

    def unrelated_witness(payload):
        for check in payload["condition_checks"]:
            check.update(
                status="covered", answer_quote="设备名为 Orion。[E1]", reason="claimed covered"
            )

    model = OpenAICompatibleClaimVerifier(settings, transport=ConditionTransport(unrelated_witness))
    with pytest.raises(InvalidProviderResponse, match="CONDITION_WITNESS"):
        model.verify("总结维修服务", draft("设备名为 Orion。[E1]"), (_evidence(text=SOURCE),))


def test_table_condition_rows_and_branches_are_not_reduced_to_headings():
    text = (
        "| 产品 | 适用地区 |\n| --- | --- |\n| A | 城区 |\n| B | 全国 |\n\n"
        "如果断电，进入人工处理；否则继续自动运行。"
    )
    quotes = condition_quotes(text)
    assert "| A | 城区 |" in quotes and "| B | 全国 |" in quotes
    assert "| 产品 | 适用地区 |" not in quotes
    assert any("人工处理" in q and "自动运行" in q for q in quotes)


def test_existing_published_image_and_its_parent_cannot_bypass_new_binding_checks(tmp_path):
    from ragkb.adapters.local_storage import LocalFileStorage
    from ragkb.config import EnvSettings
    from ragkb.infrastructure.visual_assets import VisualAssetStore
    from ragkb.infrastructure.visual_evidence import VisualEvidenceEnricher
    from test_visual_pipeline import png

    correct = [["Product", "Power"], ["A", "100 W"], ["B", "200 W"]]
    wrong = [["Product", "Power"], ["A", "200 W"], ["B", "100 W"]]
    store = VisualAssetStore(LocalFileStorage(tmp_path))
    asset = store.save_image("v", png(), {"part": "word/media/image1.png"})
    asset.update(
        status="verified",
        extraction=table_extraction(wrong).model_dump(),
        local_check={"status": "consistent", "revision": "old"},
        regions=measured(correct)["regions"],
    )
    store.write_manifest("v", [asset])

    class Analyzer:
        revision = "fixture"
        settings = EnvSettings(ocr_local_check_enabled=True)

        def query(self, *args):
            pytest.fail("Mismatched table reached the model")

    session = VisualEvidenceEnricher(store, Analyzer(), 4).session("A的功率")
    child = _evidence(document_version_id="v", locator={"visual_asset_ids": [asset["id"]]})
    assert not session((child,))
    assert not session(
        (replace(child, chunk_id="parent", locator={**child.locator, "is_parent": True}),)
    )
    assert (
        session.attempted == 0
        and "VISUAL_EVIDENCE_EXCLUDED:verification_failed" in session.warnings
    )
    assert store.get("v", asset["id"])["local_check"]["revision"] == "old"


def test_chapter_reduction_retains_separate_conditions_even_when_model_omits_them(workspace):  # noqa: F811
    from ragkb.application.reading_scope import ReadingOptions, reading_scope

    client, runtime, _, space = workspace
    text = "# 服务介绍\n" + "\n\n".join(
        f"项目{i}：" + "本节介绍常规维护工具和设备外观。" * 35 for i in range(8)
    )
    text += "\n\n# 维修限制\n仅限保修期内且位于城区的设备。进水损坏不包括在免费维修内。"
    done = upload(client, runtime, space, "conditions.md", text)
    publish(client, done["document_version_id"])
    provider = runtime.qa_service.evidence_provider
    overview = provider.overview_reader

    class EmptyReader:
        revision = "deliberate-condition-omission"
        calls = 0

        def read(self, question, evidence):
            self.calls += 1
            return {"quotes": [], "brief": "", "gaps": []}

    reader = EmptyReader()
    overview.reader = reader
    overview.settings = overview.settings.model_copy(update={"overview_evidence_tokens": 1000})
    with reading_scope(ReadingOptions(mode="overview", document_ids=(done["document_id"],))):
        package = provider.build_package(
            "总结维修政策", runtime.tenant_id, "local-user", space_id=space
        )
    assert reader.calls > 0
    assert "进水损坏不包括" in "\n".join(e.text for e in package.evidence)
    assert sum(s["condition_count"] for s in package.coverage_report["sections"]) >= 2


@pytest.mark.parametrize(
    "uncertainties,expected", [([], "supported"), (["本图功率数字模糊"], "uncertain")]
)
def test_image_topics_absent_from_this_image_do_not_veto_its_clear_facts(uncertainties, expected):
    from ragkb.adapters.visual_http import VisualAnalyzer
    from test_visual_pipeline import PASS, Transport, png, settings

    transport = Transport(
        {
            "status": "supported",
            "text": "A: 100 W; B: 200 W",
            "uncertainties": uncertainties,
            "unanswered_topics": ["维修条件", "处理分支"],
        },
        PASS,
    )
    result = VisualAnalyzer(settings(), transport).query(
        png(), "功率及维修条件", "A: 100 W; B: 200 W"
    )
    assert result.status == expected
    assert len(transport.calls) == (2 if expected == "supported" else 1)
