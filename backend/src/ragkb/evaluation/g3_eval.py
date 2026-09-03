"""Frozen G3 synthetic evaluation contract and deterministic Harness."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema
import yaml


def load_g3_eval_dataset(dataset_path: Path, schema_path: Path) -> dict[str, Any]:
    dataset = yaml.safe_load(dataset_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(dataset, dict):
        raise ValueError("G3 evaluation dataset must be a mapping")
    jsonschema.Draft202012Validator(schema).validate(dataset)
    return dataset


def run_g3_eval_harness(
    dataset: dict[str, Any], evaluator: Callable[[dict[str, Any]], str]
) -> dict[str, object]:
    cases = dataset["cases"]
    results = []
    for case in cases:
        actual = evaluator(case)
        results.append(
            {
                "case_id": case["case_id"],
                "expected_status": case["expected_status"],
                "actual_status": actual,
                "passed": actual == case["expected_status"],
            }
        )
    return {
        "dataset_id": dataset["dataset_id"],
        "dataset_revision": dataset["revision"],
        "frozen_at": dataset["frozen_at"],
        "random_seed": dataset["random_seed"],
        "case_count": len(results),
        "passed_count": sum(1 for item in results if item["passed"]),
        "results": results,
        "real_acceptance": False,
        "real_llm_call_performed": False,
        "adjudication_records_present": all(bool(case["adjudication"]) for case in cases),
    }
