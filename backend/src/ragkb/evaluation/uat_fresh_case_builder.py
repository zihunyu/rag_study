"""Build future structured-claim inputs from fresh locator-aligned source evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping


def build_fresh_case(record: Mapping[str, object], source_text: str) -> dict[str, object]:
    locator = record["locator"]
    question = record["question"]
    evidence = {
        "evidence_id": hashlib.sha256(
            (str(record["test_case_id"]) + source_text).encode()
        ).hexdigest()[:24],
        "source_document_id": record["fixture_ref"],
        "source_version_sha256": record["source_sha256"],
        "content": source_text,
        "locator": locator,
        "entity_id": None,
        "field_key": None,
        "rendered_text": source_text,
        "render_proof": record["render_proof"],
    }
    return {
        "test_case_id": record["test_case_id"],
        "question": question,
        "source_classification": record["classification"],
        "allow_cross_document": False,
        "evidence": [evidence],
    }
