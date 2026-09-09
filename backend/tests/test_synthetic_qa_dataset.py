"""Ensure candidate QA cases stay anchored to readable, correctly scoped source files.

These checks validate the dataset, not the accuracy of model answers to its cases.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader
from ragkb.document_processing.office_parsers import SpreadsheetParser
from ragkb.document_processing.text_parsers import PlainTextParser, TextPDFParser

PACK = Path(__file__).parent / "fixtures/synthetic_qa_v1"
MANIFEST = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
DATASET = json.loads((PACK / "cases.json").read_text(encoding="utf-8"))
DOCUMENTS = {doc["id"]: doc for doc in MANIFEST["documents"]}


def _normalized(text):
    return re.sub(r"\s+", "", text)


@pytest.fixture(scope="module")
def parsed():
    result = {}
    for identity, doc in DOCUMENTS.items():
        path = PACK / doc["path"]
        if doc["format"] == "pdf":
            parser = TextPDFParser()
        elif doc["format"] == "csv":
            parser = SpreadsheetParser()
        else:
            parser = PlainTextParser("markdown" if doc["format"] == "md" else "text")
        result[identity] = parser.parse(path, f"fixture-{identity}-v1")
    return result


@pytest.mark.parametrize("identity", DOCUMENTS)
def test_each_source_is_intact_and_readable_by_the_project_parser(identity, parsed):
    doc = DOCUMENTS[identity]
    path = (PACK / doc["path"]).resolve()
    assert path.is_relative_to((PACK / "sources").resolve())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == doc["sha256"]
    assert parsed[identity].nodes
    assert all(node.original_text.strip() for node in parsed[identity].nodes)


@pytest.mark.parametrize("fact_id", MANIFEST["facts"])
def test_reference_quote_and_locator_agree_with_the_actual_source(fact_id, parsed):
    fact = MANIFEST["facts"][fact_id]
    doc = DOCUMENTS[fact["doc_id"]]
    path, locator = PACK / doc["path"], fact["locator"]
    parsed_text = "\n".join(node.original_text for node in parsed[fact["doc_id"]].nodes)
    assert _normalized(fact["quote"]) in _normalized(parsed_text)
    if locator["kind"] == "line":
        assert path.read_text(encoding="utf-8").splitlines()[locator["line"] - 1] == fact["quote"]
    elif locator["kind"] == "csv_row":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        assert rows[0] == locator["columns"]
        assert rows[locator["row"] - 1] == fact["values"]
        assert " | ".join(rows[locator["row"] - 1]) == fact["quote"]
    else:
        page = PdfReader(path).pages[locator["page"] - 1]
        assert _normalized(fact["quote"]) in _normalized(page.extract_text())


@pytest.mark.parametrize("case", DATASET["cases"], ids=lambda c: c["id"])
def test_case_expectations_respect_file_scope_and_publication(case):
    assert case["execution_state"] == "not_run"
    assert case["review_state"] == "source_checked_candidate"
    selected = set(case["reading"]["document_keys"])
    assert selected.issubset(DOCUMENTS)
    allowed = {
        doc["id"]
        for doc in DOCUMENTS.values()
        if doc["knowledge_base"] == case["knowledge_base"]
        and doc["publication_state"] == "published"
        and (not selected or doc["id"] in selected)
    }
    expected_docs = set()
    for point in case["required_points"]:
        assert point["meaning"].strip() and point["fact_ids"]
        sources = {MANIFEST["facts"][identity]["doc_id"] for identity in point["fact_ids"]}
        assert sources.issubset(allowed)
        if point["check_target"] == "answer":
            expected_docs.update(sources)
        else:
            assert point["check_target"] == "retrieved_evidence"
            assert sources.issubset(case["required_retrieved_documents"])
    assert set(case["required_source_documents"]) == expected_docs
    assert case["minimum_distinct_cited_documents"] <= len(expected_docs)
    if not allowed:
        assert case["allowed_statuses"] == ["insufficient_evidence"]
        assert not expected_docs
    if case["allowed_statuses"] == ["conflicting_evidence"]:
        assert not expected_docs and case["minimum_distinct_cited_documents"] == 0
        assert len(case["required_retrieved_documents"]) >= 2


def test_missing_fields_are_absent_from_the_entire_normal_source_corpus(parsed):
    text = "\n".join(
        node.original_text
        for identity, doc in DOCUMENTS.items()
        if doc["knowledge_base"] == "main" and doc["publication_state"] == "published"
        for node in parsed[identity].nodes
    )
    for field in MANIFEST["deliberately_missing_fields"]["main"]:
        assert field not in text
    assert "九十九年" not in text and "八年" not in text
    assert "北辰档案" not in text and "远帆草案" not in text


def test_calculation_uses_the_labeled_numeric_column_in_the_correct_rows():
    case = next(c for c in DATASET["cases"] if c["id"] == "Q17")
    calculation = case["calculation"]
    left, right = (MANIFEST["facts"][f] for f in calculation["source_fact_ids"])
    left_column = left["locator"]["columns"].index(calculation["column"])
    right_column = right["locator"]["columns"].index(calculation["column"])
    assert left["values"][0] == right["values"][0] == "澄星 R9"
    assert left["values"][1] == "常温" and right["values"][1] == "低温"
    assert (
        Decimal(left["values"][left_column]) - Decimal(right["values"][right_column])
        == (calculation["expected_value"])
    )


def test_case_book_is_not_in_the_upload_manifest_and_ids_are_unique():
    assert len(DOCUMENTS) == len(MANIFEST["documents"])
    assert len({c["id"] for c in DATASET["cases"]}) == len(DATASET["cases"])
    assert all(doc["path"].startswith("sources/") for doc in DOCUMENTS.values())
    assert {"cases.json", "案例清单.md"}.issubset(MANIFEST["excluded_from_knowledge_base"])
    for kb in MANIFEST["knowledge_bases"]:
        for state, key in [
            ("published", "published_documents"),
            ("draft", "unpublished_documents"),
        ]:
            assert set(kb[key]) == {
                d["id"]
                for d in DOCUMENTS.values()
                if d["knowledge_base"] == kb["id"] and d["publication_state"] == state
            }
