"""Deterministic locator-grounded bundles and exact real-UAT model plan."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import yaml
from openpyxl.utils.cell import range_boundaries

from ragkb.config import load_env
from ragkb.document_processing.parsers import ParserRouter
from ragkb.engineering_security.file_validation import FORMAT_BY_EXTENSION
from ragkb.evaluation.format_samples import _resolve
from ragkb.evaluation.local_sample_validation import (
    _anonymous_id,
    _expected_locator_match,
    _locator_matches,
)
from ragkb.evaluation.uat_candidates import require_user_review_before_model_calls
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode(), usedforsecurity=False).hexdigest()


def _metadata(root: Path, category: str) -> tuple[Path, list[Mapping[str, object]]]:
    plan = yaml.safe_load(
        (root / "backend/tests/fixtures/manifests/format-samples.yaml").read_text(encoding="utf-8")
    )
    item = next(value for value in plan["collection_plan"] if value["format"] == category)
    loaded = yaml.safe_load(_resolve(root, str(item["metadata_path"])).read_text(encoding="utf-8"))
    samples = [sample for sample in loaded["samples"] if isinstance(sample, Mapping)]
    return _resolve(root, str(item["sample_directory"])), samples


def _local_nodes(root: Path, category: str) -> dict[str, list[dict[str, object]]]:
    directory, samples = _metadata(root, category)
    router = ParserRouter()
    result: dict[str, list[dict[str, object]]] = {}
    for sample in samples:
        path = (directory / str(sample["file"])).resolve()
        source_format = FORMAT_BY_EXTENSION[path.suffix.casefold()][0]
        document = router.parse(source_format, path, "private-uat-bundle")
        sample_id = _anonymous_id(category, sample)
        result[sample_id] = [
            {
                "node_id": hashlib.sha256(
                    (
                        f"{sample_id}:{index}:"
                        f"{json.dumps(node.locator.to_dict(), sort_keys=True)}:"
                        f"{_sha256_text(node.display_text)}"
                    ).encode(),
                    usedforsecurity=False,
                ).hexdigest()[:32],
                "display_text": node.display_text,
                "locator": node.locator.to_dict(),
            }
            for index, node in enumerate(document.nodes)
        ]
    return result


def _provider_nodes(
    root: Path,
    category: str,
    checkpoint_names: Sequence[str],
    evidence_category: str,
) -> dict[str, list[dict[str, object]]]:
    loaded = load_env(root)
    if loaded.settings is None:
        raise ValueError("CONFIG_INVALID")
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (root / artifacts_root).resolve()
    from ragkb.infrastructure.provider_results import LocalProviderResultStore

    store = LocalProviderResultStore(artifacts_root)
    checkpoint_root = root / "artifacts/final-validation/provider-checkpoints"
    evidence: dict[str, Mapping[str, object]] = {}
    for name in checkpoint_names:
        content = json.loads((checkpoint_root / name).read_text(encoding="utf-8"))
        namespace = content.get("mineru", {})
        for key, value in namespace.items():
            if (
                key != "_manifest"
                and isinstance(value, Mapping)
                and value.get("state") == "COMPLETED"
                and isinstance(value.get("evidence"), Mapping)
            ):
                evidence[key] = value["evidence"]
    _, samples = _metadata(root, category)
    result: dict[str, list[dict[str, object]]] = {}
    for sample in samples:
        expected_id = _anonymous_id(category, sample)
        provider_id = _anonymous_id(evidence_category, sample)
        item = evidence.get(provider_id)
        if item is None or not isinstance(item.get("artifact_id"), str):
            raise ValueError("UAT_PROVIDER_SAMPLE_EVIDENCE_MISSING")
        nodes = store.read_mineru_nodes(str(item["artifact_id"]))
        result[expected_id] = [dict(node) for node in nodes]
    return result


def _cell_overlap(expected: Mapping[str, object], actual: Mapping[str, object]) -> bool:
    if expected.get("sheet") != actual.get("sheet"):
        return False
    expected_range = expected.get("cell_range")
    actual_range = actual.get("cell_range")
    if not isinstance(expected_range, str) or not isinstance(actual_range, str):
        return False
    try:
        ex_min_c, ex_min_r, ex_max_c, ex_max_r = range_boundaries(expected_range)
        ac_min_c, ac_min_r, ac_max_c, ac_max_r = range_boundaries(actual_range)
    except ValueError:
        return False
    bounds = (ex_min_c, ex_min_r, ex_max_c, ex_max_r, ac_min_c, ac_min_r, ac_max_c, ac_max_r)
    if any(value is None for value in bounds):
        return False
    (
        ex_min_c,
        ex_min_r,
        ex_max_c,
        ex_max_r,
        ac_min_c,
        ac_min_r,
        ac_max_c,
        ac_max_r,
    ) = cast(tuple[int, int, int, int, int, int, int, int], bounds)
    return not (
        ac_max_c < ex_min_c or ac_min_c > ex_max_c or ac_max_r < ex_min_r or ac_min_r > ex_max_r
    )


def _positive_document(
    candidate: Mapping[str, object], nodes: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    expected = candidate.get("expected_locator")
    if not isinstance(expected, Mapping):
        raise ValueError("UAT_EXPECTED_LOCATOR_INVALID")
    actual_locators = []
    for node in nodes:
        locator = node.get("locator")
        if isinstance(locator, Mapping):
            actual_locators.append(dict(locator))
    matched, total = _expected_locator_match([expected], actual_locators)
    if (matched, total) != (1, 1):
        raise ValueError("UAT_EXPECTED_LOCATOR_NOT_STRICTLY_COVERED")
    matching_nodes = []
    for node in nodes:
        locator = node.get("locator")
        if not isinstance(locator, Mapping):
            continue
        matches = (
            _cell_overlap(expected, locator)
            if "cell_range" in expected
            else _locator_matches(expected, locator)
        )
        text = node.get("display_text")
        if matches and isinstance(text, str) and text.strip():
            matching_nodes.append(node)
    if not matching_nodes:
        raise ValueError("UAT_POSITIVE_CONTENT_EMPTY")
    matching_nodes.sort(key=lambda node: str(node.get("node_id", "")))
    content = "\n".join(str(node["display_text"]) for node in matching_nodes)
    candidate_id = str(candidate["candidate_id"])
    expected_evidence = candidate.get("expected_evidence")
    if not isinstance(expected_evidence, Mapping) or not isinstance(
        expected_evidence.get("anonymous_sample_id"), str
    ):
        raise ValueError("UAT_EXPECTED_EVIDENCE_INVALID")
    return {
        "evidence_id": hashlib.sha256(
            f"{candidate_id}:positive".encode(), usedforsecurity=False
        ).hexdigest()[:20],
        "source_candidate_id": candidate_id,
        "source_category": candidate["source_category"],
        "anonymous_sample_id": expected_evidence["anonymous_sample_id"],
        "locator": dict(expected),
        "node_ids": [str(node["node_id"]) for node in matching_nodes],
        "content": content,
        "content_sha256": _sha256_text(content),
    }


def build_uat_bundles(root: Path) -> dict[str, object]:
    approved_path = root / "artifacts/final-validation/uat-candidates/approved.json"
    approved = json.loads(approved_path.read_text(encoding="utf-8"))
    require_user_review_before_model_calls(approved)
    candidates = approved.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 78:
        raise ValueError("UAT_APPROVED_CANDIDATES_INVALID")
    nodes_by_category = {
        "pdf_text": _local_nodes(root, "pdf_text"),
        "pptx": _local_nodes(root, "pptx"),
        "spreadsheet": _local_nodes(root, "spreadsheet"),
        "pdf_scanned_or_image": _provider_nodes(
            root,
            "pdf_scanned_or_image",
            ("mineru-scan-attempt-v4.json", "mineru-scan-attempt-v5.json"),
            "pdf_scanned_or_image",
        ),
        "docx": _provider_nodes(
            root,
            "docx",
            ("mineru-docx-pdf-attempt-v1.json",),
            "docx_pdf",
        ),
    }
    positives: dict[str, dict[str, object]] = {}
    for raw_candidate in candidates:
        if not isinstance(raw_candidate, Mapping):
            raise ValueError("UAT_APPROVED_CANDIDATE_INVALID")
        category = str(raw_candidate.get("source_category"))
        expected_evidence = raw_candidate.get("expected_evidence")
        if not isinstance(expected_evidence, Mapping):
            raise ValueError("UAT_EXPECTED_EVIDENCE_INVALID")
        sample_id = expected_evidence.get("anonymous_sample_id")
        if not isinstance(sample_id, str):
            raise ValueError("UAT_EXPECTED_SAMPLE_ID_INVALID")
        nodes = nodes_by_category.get(category, {}).get(sample_id)
        if nodes is None:
            raise ValueError("UAT_SAMPLE_NODES_MISSING")
        positive = _positive_document(raw_candidate, nodes)
        positives[str(raw_candidate["candidate_id"])] = positive
    loaded = load_env(root)
    if loaded.settings is None:
        raise ValueError("CONFIG_INVALID")
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (root / artifacts_root).resolve()
    store = LocalUatArtifactStore(artifacts_root, bundle_revision="v2")
    bundle_records = []
    for raw_candidate in candidates:
        candidate = dict(raw_candidate)
        candidate_id = str(candidate["candidate_id"])
        positive = positives[candidate_id]
        pool = [
            item
            for other_id, item in positives.items()
            if other_id != candidate_id
            and item["source_category"] == positive["source_category"]
            and item["anonymous_sample_id"] != positive["anonymous_sample_id"]
        ]
        pool.sort(
            key=lambda item: hashlib.sha256(
                f"{candidate_id}:{item['evidence_id']}".encode(),
                usedforsecurity=False,
            ).hexdigest()
        )
        distractors = pool[:3]
        if len(distractors) != 3:
            raise ValueError("UAT_DISTRACTOR_COUNT_INSUFFICIENT")
        documents = [{**positive, "role": "positive"}] + [
            {**item, "role": "distractor"} for item in distractors
        ]
        bundle = {
            "revision": "locator-grounded-uat-bundle:v2",
            "candidate_id": candidate_id,
            "question": candidate["question"],
            "source_category": candidate["source_category"],
            "source_classification": candidate["source_classification"],
            "expected_locator": candidate["expected_locator"],
            "expected_evidence": candidate["expected_evidence"],
            "expected_positive_evidence_id": positive["evidence_id"],
            "documents": documents,
            "document_count": len(documents),
            "query_embedding_request_count": 0,
            "zilliz_request_count": 0,
            "controlled_locator_grounded_uat": True,
            "production_retrieval_e2e_claimed": False,
        }
        metadata = store.persist_bundle(candidate_id, bundle)
        bundle_records.append(
            {
                **metadata,
                "source_category": candidate["source_category"],
                "document_count": len(documents),
                "positive_count": 1,
                "distractor_count": 3,
                "positive_locator_hash": _sha256_text(
                    json.dumps(candidate["expected_locator"], sort_keys=True)
                ),
                "content_in_plan": False,
            }
        )
    if len(bundle_records) != 78:
        raise ValueError("UAT_BUNDLE_COUNT_MISMATCH")
    snapshot_hash = hashlib.sha256(
        json.dumps(bundle_records, separators=(",", ":"), sort_keys=True).encode(),
        usedforsecurity=False,
    ).hexdigest()
    return {
        "revision": "real-uat-plan:v2",
        "bundle_count": 78,
        "bundle_snapshot_hash": snapshot_hash,
        "bundles": bundle_records,
        "documents_per_bundle": 4,
        "positive_per_bundle": 1,
        "distractors_per_bundle": 3,
        "controlled_locator_grounded_uat": True,
        "production_retrieval_e2e_claimed": False,
        "query_embedding_request_count": 0,
        "zilliz_request_count": 0,
        "reranker": {
            "checkpoint_ref": "provider-checkpoints/uat-reranker-v1.json",
            "max_requests": 78,
            "automatic_retries": 0,
            "executed": False,
            "runner_review_required": True,
            "global_failure_policy": "STOP_ALL_AND_DO_NOT_START_LLM",
            "positive_must_enter_top_k": True,
        },
        "candidate2_reranker_diagnostic_v2": {
            "attempt_revision": "uat-reranker-diagnostic-runner:v2",
            "checkpoint_ref": "provider-checkpoints/uat-reranker-v2.json",
            "prior_failed_checkpoint_ref": "provider-checkpoints/uat-reranker-v1.json",
            "prior_failed_checkpoint_read_only": True,
            "max_requests": 1,
            "automatic_retries": 0,
            "planned": True,
            "approved_by_user": True,
            "executed": True,
            "request_count": 1,
            "completed_count": 1,
            "gate_passed": True,
            "positive_rank": 1,
            "llm_request_count": 0,
            "runner_review_required": False,
        },
        "remaining_reranker_continuation_v3": {
            "attempt_revision": "uat-reranker-continuation-runner:v3",
            "checkpoint_ref": "provider-checkpoints/uat-reranker-v3.json",
            "prior_v1_v2_checkpoints_read_only": True,
            "remaining_candidate_count": 76,
            "max_requests": 76,
            "automatic_retries": 0,
            "planned": True,
            "approved_by_user": True,
            "executed": True,
            "request_count": 2,
            "completed_count": 1,
            "failed_count": 1,
            "gate_passed_count": 1,
            "execution_status": "PARTIAL_GATE_FAILED",
            "error_code": "UAT_RERANKER_V3_POSITIVE_NOT_IN_TOP_K",
            "llm_request_count": 0,
            "runner_review_required": False,
        },
        "systematic_revision_v4": {
            "review_ref": "uat-systematic-revision-v4/approved-review.json",
            "manifest_ref": "uat-systematic-revision-v4/manifest.json",
            "passed_existing_count": 3,
            "pending_revision_count": 75,
            "checkpoint_ref": "provider-checkpoints/uat-reranker-v4.json",
            "max_requests": 75,
            "positive_top_k": 2,
            "automatic_retries": 0,
            "approved_by_user": True,
            "executed": True,
            "request_count": 37,
            "completed_count": 36,
            "failed_count": 1,
            "gate_passed_count": 36,
            "execution_status": "PARTIAL_GATE_FAILED",
            "error_code": "UAT_RERANKER_V4_POSITIVE_NOT_IN_TOP_K",
            "status": "EXECUTED_PARTIAL_GATE_FAILED",
            "llm_request_count": 0,
        },
        "systematic_revision_v5": {
            "review_ref": "uat-systematic-revision-v5/approved-review.json",
            "manifest_ref": "uat-systematic-revision-v5/manifest.json",
            "passed_existing_count": 39,
            "pending_revision_count": 39,
            "checkpoint_ref": "provider-checkpoints/uat-reranker-v5.json",
            "max_requests": 39,
            "positive_top_k": 2,
            "automatic_retries": 0,
            "approved_by_user": False,
            "executed": False,
            "status": "PENDING_USER_REVIEW",
            "llm_approved_for_revised_set": False,
        },
        "llm": {
            "checkpoint_ref": "provider-checkpoints/uat-llm-v1.json",
            "max_requests": 78,
            "automatic_retries": 0,
            "executed": False,
            "runner_review_required": True,
            "prerequisite": "ALL_78_RERANKER_COMPLETED_AND_GATE_PASSED",
            "required_output_fields": ["status", "answer", "citation_ids"],
            "citation_must_come_from_bundle": True,
            "expected_positive_must_be_cited": True,
        },
        "total_model_request_budget": 156,
        "conditional_user_authorization_satisfied": True,
        "user_result_review_required_before_uat_pass": True,
        "executed": False,
        "real_uat_passed": False,
        "content_output": False,
        "source_names_output": False,
    }
