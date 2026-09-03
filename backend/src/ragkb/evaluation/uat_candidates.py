"""Offline, content-free UAT candidate generation from authorized metadata."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from ragkb.evaluation.format_samples import _resolve


def _mapping(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("UAT_METADATA_INVALID")
    return loaded


def generate_uat_candidates(root: Path, plan_path: Path) -> dict[str, object]:
    plan = _mapping(plan_path)
    candidates: list[dict[str, object]] = []
    for item in plan.get("collection_plan", []):
        if not isinstance(item, Mapping) or item.get("deferred_by_user"):
            continue
        category = str(item.get("format", "unknown"))
        metadata = _mapping(_resolve(root, item["metadata_path"]))
        for sample in metadata.get("samples", []):
            if not isinstance(sample, Mapping):
                continue
            sample_id = hashlib.sha256(
                f"{category}:{sample.get('id')}:{sample.get('sha256')}".encode(),
                usedforsecurity=False,
            ).hexdigest()[:16]
            locators = [
                dict(locator)
                for locator in sample.get("expected_locators", [])
                if isinstance(locator, Mapping)
            ]
            for locator_index, locator in enumerate(locators):
                candidate_id = hashlib.sha256(
                    f"{sample_id}:{locator_index}:{sorted(locator)}".encode(),
                    usedforsecurity=False,
                ).hexdigest()[:20]
                locator_kind = "/".join(sorted(map(str, locator))) or "source"
                candidates.append(
                    {
                        "candidate_id": candidate_id,
                        "question": (
                            f"请复核该授权{category}样本在{locator_kind}定位处的业务信息，"
                            "并仅依据指定证据作答。"
                        ),
                        "source_category": category,
                        "source_classification": str(
                            sample.get("source_classification", "unknown")
                        ),
                        "expected_locator": locator,
                        "expected_evidence": {
                            "anonymous_sample_id": sample_id,
                            "locator": locator,
                        },
                        "status": "PENDING_USER_REVIEW",
                        "uses_source_content": False,
                        "uses_source_filename": False,
                    }
                )
    return {
        "revision": "uat-candidates:v1",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "model_call_count": 0,
        "network_call_count": 0,
        "reranker_plan_generated": False,
        "llm_plan_generated": False,
        "all_pending_user_review": all(
            item["status"] == "PENDING_USER_REVIEW" for item in candidates
        ),
        "real_acceptance": False,
    }


def require_user_review_before_model_calls(report: Mapping[str, object]) -> None:
    candidates = report.get("candidates")
    if not isinstance(candidates, list) or any(
        not isinstance(item, Mapping) or item.get("status") != "APPROVED_BY_USER"
        for item in candidates
    ):
        raise ValueError("UAT_USER_REVIEW_REQUIRED_BEFORE_MODEL_CALLS")


def approve_all_uat_candidates(
    pending_bytes: bytes, expected_sha256: str
) -> tuple[dict[str, object], dict[str, object]]:
    pending_sha256 = hashlib.sha256(pending_bytes, usedforsecurity=False).hexdigest()
    if pending_sha256 != expected_sha256.casefold():
        raise ValueError("UAT_PENDING_SNAPSHOT_MISMATCH")
    loaded = json.loads(pending_bytes.decode("utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("UAT_PENDING_INVALID")
    candidates = loaded.get("candidates")
    if (
        not isinstance(candidates, list)
        or len(candidates) != 78
        or any(
            not isinstance(candidate, dict) or candidate.get("status") != "PENDING_USER_REVIEW"
            for candidate in candidates
        )
    ):
        raise ValueError("UAT_PENDING_NOT_78_ALL_PENDING")
    approved = copy.deepcopy(loaded)
    approved_candidates = approved["candidates"]
    approved_ids: list[str] = []
    for candidate in approved_candidates:
        candidate["status"] = "APPROVED_BY_USER"
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("UAT_CANDIDATE_ID_INVALID")
        approved_ids.append(candidate_id)
    if len(set(approved_ids)) != 78:
        raise ValueError("UAT_CANDIDATE_ID_DUPLICATE")
    approved["all_pending_user_review"] = False
    approved["all_approved_by_user"] = True
    approved["approval_decision"] = "APPROVE_ALL_78"
    require_user_review_before_model_calls(approved)
    approved_bytes = (
        json.dumps(approved, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    approved_ids_hash = hashlib.sha256(
        json.dumps(sorted(approved_ids), separators=(",", ":")).encode(),
        usedforsecurity=False,
    ).hexdigest()
    manifest = {
        "revision": "uat-approval-manifest:v1",
        "decision": "APPROVE_ALL_78",
        "candidate_count": 78,
        "pending_sha256": pending_sha256,
        "approved_sha256": hashlib.sha256(approved_bytes, usedforsecurity=False).hexdigest(),
        "approved_ids_hash": approved_ids_hash,
        "question_text_in_manifest": False,
        "network_call_count": 0,
        "reranker_call_count": 0,
        "llm_call_count": 0,
    }
    return approved, manifest


def validate_uat_approval(
    pending_bytes: bytes,
    approved_bytes: bytes,
    manifest_bytes: bytes,
    expected_pending_sha256: str,
) -> dict[str, object]:
    expected_approved, expected_manifest = approve_all_uat_candidates(
        pending_bytes, expected_pending_sha256
    )
    rendered_approved = (
        json.dumps(expected_approved, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    loaded_manifest = json.loads(manifest_bytes.decode("utf-8"))
    if approved_bytes != rendered_approved or loaded_manifest != expected_manifest:
        raise ValueError("UAT_APPROVAL_ARTIFACT_MISMATCH")
    loaded_approved = json.loads(approved_bytes.decode("utf-8"))
    require_user_review_before_model_calls(loaded_approved)
    return {
        "decision": "APPROVE_ALL_78",
        "candidate_count": 78,
        "pending_sha256": expected_manifest["pending_sha256"],
        "approved_sha256": expected_manifest["approved_sha256"],
        "approved_ids_hash": expected_manifest["approved_ids_hash"],
        "pending_snapshot_unchanged": True,
        "all_approved_by_user": True,
        "question_text_output": False,
        "network_call_count": 0,
        "reranker_call_count": 0,
        "llm_call_count": 0,
    }
