from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from ragkb.evaluation.low_cost_acceptance import (
    acceptance_gate_passed,
    evaluate_expected_answers,
)
from ragkb.evaluation.quality_thresholds import (
    QUALITY_METRICS,
    load_quality_threshold_policy,
)
from ragkb.evaluation.real_gold import REQUIRED_QUERY_TYPES

from scripts.sign_real_acceptance import build_parser, validate_signable_report


def _policy():
    root = Path(__file__).resolve().parents[2]
    return load_quality_threshold_policy(root / "config/rag-quality-thresholds.json")


def _report() -> dict[str, object]:
    policy = _policy()
    generations = {"1": "tested-1", "5": "tested-5", "20": "tested-20"}
    scales = (1, 5, 20, 1, 5, 20, 1, 5, 20, 20)
    return {
        "passed": True,
        "real_acceptance": True,
        "quality_passed": True,
        "expected_answers_passed": True,
        "cases_passed": True,
        "prompt_injection_passed": True,
        "metrics": {name: 1.0 for name in QUALITY_METRICS},
        "thresholds": dict(policy.values),
        "threshold_sha256": policy.sha256,
        "threshold_revision": policy.revision,
        "case_count": 10,
        "query_types": sorted(REQUIRED_QUERY_TYPES),
        "answer_case_results": [
            {
                "case_id": f"case-{index}",
                "performance_scale": scale,
                "index_generation_id": generations[str(scale)],
                "score": 1.0,
                "passed": True,
            }
            for index, scale in enumerate(scales, start=1)
        ],
        "query_type_buckets": {name: {} for name in REQUIRED_QUERY_TYPES},
        "tested_generations": generations,
        "index_generation_id": "tested-20",
        "performance": {"performance_scope": [1, 5, 20], "slo_claimed": False},
        "budget": {
            "limits": {
                "provider_calls": 60,
                "input_tokens": 200_000,
                "output_tokens": 20_000,
            },
            "usage": {"provider_calls": 56, "input_tokens": 100_000, "output_tokens": 10_000},
            "automatic_retries": 0,
        },
        "automatic_retries": 0,
        "cleanup": {"all_removed": True},
        "provider": "real-low-cost-rag",
        "embedding_revision": "embedding:v1",
        "reranker_revision": "reranker:v1",
        "model_revision": "generator:v1",
        "verifier_revision": "verifier:v1",
        "tokenizer_revision": "tokenizer:v1",
        "prompt_revision": "prompt:v1",
        "dataset_revision": "gold:v1",
        "budget_report_sha256": "b" * 64,
        "source_commit": "a" * 40,
    }


def test_threshold_policy_is_complete_non_zero_and_rejects_zero(tmp_path: Path) -> None:
    policy = _policy()
    assert set(policy.values) == QUALITY_METRICS
    assert all(0 < value <= 1 for value in policy.values.values())

    invalid = tmp_path / "thresholds.json"
    invalid.write_text(
        "{" + ",".join(f'"{name}":0' for name in sorted(QUALITY_METRICS)) + "}",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="MUST_BE_NON_ZERO"):
        load_quality_threshold_policy(invalid)


def test_expected_answer_and_quality_are_both_hard_gates() -> None:
    cases = [
        {
            "case_id": "answer",
            "performance_scale": 20,
            "expected_status": "answered",
            "expected_answer": "保修期为三年",
            "actual_answer": "提供标准保修服务",
        }
    ]
    results, passed = evaluate_expected_answers(cases, {20: "tested-20"}, minimum_f1=0.85)

    assert float(results[0]["score"]) < 0.85
    assert passed is False
    assert (
        acceptance_gate_passed(
            quality_passed=False,
            expected_answers_passed=True,
            correctness_passed=True,
            prompt_injection_passed=True,
            cleanup_passed=True,
        )
        is False
    )


def test_signing_uses_only_reported_tested_generation_and_bound_thresholds() -> None:
    policy = _policy()
    report = _report()
    metadata = validate_signable_report(
        report,
        policy,
        min_cases=10,
        required_query_types=tuple(REQUIRED_QUERY_TYPES),
        source_commit="a" * 40,
    )
    assert metadata["index_generation_id"] == "tested-20"
    parser_destinations = {action.dest for action in build_parser()._actions}
    assert "index_generation_id" not in parser_destinations
    assert "provider" not in parser_destinations

    arbitrary = deepcopy(report)
    arbitrary["index_generation_id"] = "untested-production-generation"
    with pytest.raises(ValueError, match="GENERATION_NOT_TESTED"):
        validate_signable_report(
            arbitrary,
            policy,
            min_cases=10,
            required_query_types=tuple(REQUIRED_QUERY_TYPES),
            source_commit="a" * 40,
        )

    zero_threshold = deepcopy(report)
    zero_threshold["thresholds"] = {name: 0.0 for name in QUALITY_METRICS}
    with pytest.raises(ValueError, match="THRESHOLD_POLICY_MISMATCH"):
        validate_signable_report(
            zero_threshold,
            policy,
            min_cases=10,
            required_query_types=tuple(REQUIRED_QUERY_TYPES),
            source_commit="a" * 40,
        )
