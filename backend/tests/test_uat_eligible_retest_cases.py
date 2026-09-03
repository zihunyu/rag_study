from pathlib import Path

from ragkb.document_processing.parsers import ParserRouter
from ragkb.domain.ids import new_uuid7


def test_generated_source_fixture_has_locator_aligned_fresh_evidence(tmp_path: Path):
    source = tmp_path / "source.csv"
    source.write_text("a,b\nleft,right\n", encoding="utf-8")
    doc = ParserRouter().parse("csv", source, new_uuid7())
    node = doc.nodes[1]
    case = {
        "question": "generated question",
        "evidence": [
            {
                "content": node.display_text,
                "locator": node.locator.to_dict(),
                "source_classification": "internal",
            }
        ],
        "old_model_answer_included": False,
    }
    assert case["evidence"][0]["locator"]["row"] == 2 and "answer" not in case
