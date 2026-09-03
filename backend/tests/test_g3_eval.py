from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
from ragkb.evaluation.g3_eval import load_g3_eval_dataset, run_g3_eval_harness


def _paths() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[2]
    return (
        root / "backend/tests/fixtures/manifests/g3-eval-dataset.yaml",
        root / "backend/src/ragkb/contracts/schemas/g3-eval-dataset-v1.schema.json",
    )


def test_frozen_g3_eval_contract_covers_six_states_and_adjudication() -> None:
    dataset = load_g3_eval_dataset(*_paths())

    report = run_g3_eval_harness(dataset, lambda case: str(case["expected_status"]))

    assert report["case_count"] == report["passed_count"] == 6
    assert report["random_seed"] == 20260901
    assert report["adjudication_records_present"] is True
    assert report["real_acceptance"] is False
    assert report["real_llm_call_performed"] is False


def test_missing_adjudication_is_rejected() -> None:
    dataset = load_g3_eval_dataset(*_paths())
    invalid = deepcopy(dataset)
    del invalid["cases"][0]["adjudication"]
    _, schema_path = _paths()

    with pytest.raises(jsonschema.ValidationError):
        # Reuse the validator through an isolated temporary-like direct validation.
        import json

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(invalid)
