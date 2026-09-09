from copy import deepcopy

import pytest
from ragkb.api.citation_projection import citation_locator


def _table_parent(ranges):
    return {
        "sheet": "csv",
        "cell_range": "A1:E1",
        "is_parent": True,
        "source_spans": [
            {"locator": {"sheet": "csv", "cell_range": value}, "chunk_id": "private"}
            for value in ranges
        ],
    }


def test_parent_citation_covers_all_displayed_rows_without_internal_metadata():
    locator = _table_parent(["A1:E1", "A2:E2", "A3:E3", "A4:E4", "A5:E5"])
    original = deepcopy(locator)
    assert citation_locator(locator) == {"sheet": "csv", "cell_range": "A1:E5"}
    assert locator == original


@pytest.mark.parametrize("ranges", [[], ["A1:E1", "A3:E3"], ["A1:E1", "B2:E2"], ["bad"]])
def test_parent_citation_does_not_invent_a_contiguous_rectangle(ranges):
    assert citation_locator(_table_parent(ranges)) == {"sheet": "csv"}


def test_parent_citation_never_joins_different_sheets_and_child_stays_exact():
    locator = _table_parent(["A1:E1", "A2:E2"])
    locator["source_spans"][1]["locator"]["sheet"] = "other"
    assert citation_locator(locator) == {"sheet": "csv"}
    assert citation_locator({"sheet": "csv", "cell_range": "A2:E2"}) == {
        "sheet": "csv",
        "cell_range": "A2:E2",
    }
