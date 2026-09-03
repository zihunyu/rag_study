"""Prepare dynamic, source-fresh future UAT error-case inputs without provider calls."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.config import load_env  # noqa: E402
from ragkb.evaluation.local_sample_validation import _anonymous_id, _locator_matches  # noqa: E402
from ragkb.evaluation.real_uat import (  # noqa: E402
    _cell_overlap,
    _local_nodes,
    _metadata,
    _provider_nodes,
)
from ragkb.evaluation.uat_error_case_retest import (  # noqa: E402
    prepare_retest_cases,
    select_retest_case_ids,
)
from ragkb.evaluation.uat_render_proof import independent_render_proof  # noqa: E402

REVIEW = ROOT / "artifacts/user-review/uat-v4-package-20260902/UAT_v4_逐项审核结果.jsonl"
SOURCE_PLAN = ROOT / "artifacts/final-validation/uat-systematic-revision-v5-plan.json"
OUTPUT_PLAN = ROOT / "artifacts/final-validation/uat-future-error-retest-v4-plan.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes(), usedforsecurity=False).hexdigest()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_bytes(value: object, *, indent: int | None = None) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=indent,
            separators=None if indent else (",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _fresh_nodes(root: Path) -> dict[str, dict[str, list[dict[str, object]]]]:
    return {
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


def _source_versions(root: Path) -> dict[str, dict[str, tuple[str, Path]]]:
    versions: dict[str, dict[str, tuple[str, Path]]] = {}
    for category in ("pdf_text", "pptx", "spreadsheet", "pdf_scanned_or_image", "docx"):
        directory, samples = _metadata(root, category)
        versions[category] = {
            _anonymous_id(category, sample): (
                str(sample["sha256"]),
                directory / str(sample["file"]),
            )
            for sample in samples
            if isinstance(sample.get("sha256"), str)
        }
    return versions


def _fresh_source_for_bundle(
    bundle: Mapping[str, object],
    nodes_by_category: Mapping[str, Mapping[str, list[dict[str, object]]]],
    versions: Mapping[str, Mapping[str, tuple[str, Path]]],
) -> dict[str, object]:
    question = bundle.get("question")
    category = bundle.get("source_category")
    source_classification = bundle.get("source_classification")
    expected = bundle.get("expected_locator")
    documents = bundle.get("documents")
    if (
        not isinstance(question, str)
        or not isinstance(category, str)
        or not isinstance(source_classification, str)
        or not isinstance(expected, Mapping)
        or not isinstance(documents, list)
    ):
        raise RuntimeError("UAT_ERROR_RETEST_BUNDLE_SCHEMA_INVALID")
    positive = next(
        (
            document
            for document in documents
            if isinstance(document, Mapping) and document.get("role") == "positive"
        ),
        None,
    )
    if not isinstance(positive, Mapping) or not isinstance(
        positive.get("anonymous_sample_id"), str
    ):
        raise RuntimeError("UAT_ERROR_RETEST_POSITIVE_PROVENANCE_INVALID")
    sample_id = str(positive["anonymous_sample_id"])
    nodes = nodes_by_category.get(category, {}).get(sample_id)
    source = versions.get(category, {}).get(sample_id)
    if nodes is None or source is None:
        raise RuntimeError("UAT_ERROR_RETEST_FRESH_SOURCE_MISSING")
    source_version, source_path = source
    matching = []
    for node in nodes:
        locator = node.get("locator")
        text = node.get("display_text")
        if not isinstance(locator, Mapping) or not isinstance(text, str):
            continue
        matches = (
            _cell_overlap(expected, locator)
            if "cell_range" in expected
            else _locator_matches(expected, locator)
        )
        if matches:
            matching.append(node)
    matching.sort(key=lambda item: str(item.get("node_id", "")))
    content = "\n".join(str(item.get("display_text", "")) for item in matching)
    evidence_id = hashlib.sha256(
        f"future-error-retest:{category}:{sample_id}:{_sha256_text(content)}".encode(),
        usedforsecurity=False,
    ).hexdigest()[:24]
    try:
        proof = independent_render_proof(
            category=category,
            source_path=source_path,
            source_version_sha256=source_version,
            locator=expected,
        )
    except Exception:
        proof = None
    return {
        "question": question,
        "allow_cross_document": False,
        "source_classification": source_classification,
        "evidence": {
            "evidence_id": evidence_id,
            "source_document_id": f"source-{category}-{sample_id}",
            "source_version_sha256": source_version,
            "content": content,
            "locator": dict(expected),
            "entity_id": None,
            "field_key": None,
            "rendered_text": proof["rendered_text"] if proof is not None else None,
            "render_proof": (
                {key: value for key, value in proof.items() if key != "rendered_text"}
                if proof is not None
                else None
            ),
            "requires_rendered_proof": True,
        },
    }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode(), usedforsecurity=False).hexdigest()


def main() -> int:
    rows = [
        json.loads(line) for line in REVIEW.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    review_rows = [row for row in rows if isinstance(row, dict)]
    selected_ids = select_retest_case_ids(review_rows)
    if len(selected_ids) != 15:
        raise RuntimeError("UAT_ERROR_RETEST_SELECTION_COUNT_INVALID")
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    source_plan = json.loads(SOURCE_PLAN.read_text(encoding="utf-8"))
    records = source_plan.get("selected_bundles")
    if not isinstance(records, list) or len(records) != 78:
        raise RuntimeError("UAT_ERROR_RETEST_SOURCE_PLAN_INVALID")
    nodes = _fresh_nodes(ROOT)
    versions = _source_versions(ROOT)
    by_id: dict[str, dict[str, object]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise RuntimeError("UAT_ERROR_RETEST_SOURCE_RECORD_INVALID")
        candidate_id = record.get("candidate_id")
        path = (artifacts_root / str(record.get("bundle_ref", ""))).resolve()
        if (
            not isinstance(candidate_id, str)
            or artifacts_root not in path.parents
            or _sha256(path) != record.get("bundle_sha256")
        ):
            raise RuntimeError("UAT_ERROR_RETEST_SOURCE_BUNDLE_HASH_INVALID")
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(bundle, dict) or bundle.get("candidate_id") != candidate_id:
            raise RuntimeError("UAT_ERROR_RETEST_SOURCE_BUNDLE_INVALID")
        by_id[candidate_id] = _fresh_source_for_bundle(bundle, nodes, versions)
    eligible, blocked, preflight = prepare_retest_cases(selected_ids, by_id)
    package_root = artifacts_root / "uat-future-error-retest-v4"
    case_payloads = {f"cases/{item['test_case_id']}.json": _json_bytes(item) for item in eligible}
    blocked_payload = _json_bytes(blocked)
    records_for_manifest = [
        {
            "test_case_id": item["test_case_id"],
            "case_ref": f"uat-future-error-retest-v4/cases/{item['test_case_id']}.json",
            "case_sha256": hashlib.sha256(
                case_payloads[f"cases/{item['test_case_id']}.json"], usedforsecurity=False
            ).hexdigest(),
        }
        for item in eligible
    ]
    package_manifest = {
        **preflight,
        "revision": "uat-error-retest-input-manifest:v1",
        "review_input_sha256": _sha256(REVIEW),
        "source_plan_sha256": _sha256(SOURCE_PLAN),
        "case_records": records_for_manifest,
        "case_snapshot_sha256": hashlib.sha256(
            _json_bytes(records_for_manifest), usedforsecurity=False
        ).hexdigest(),
        "blocked_ref": "uat-future-error-retest-v3/preflight-blocked.json",
        "blocked_sha256": hashlib.sha256(blocked_payload, usedforsecurity=False).hexdigest(),
        "content_output": False,
    }
    expected = {
        **case_payloads,
        "preflight-blocked.json": blocked_payload,
        "manifest.json": _json_bytes(package_manifest, indent=2),
    }
    if package_root.exists():
        if any(
            not (package_root / relative).is_file()
            or (package_root / relative).read_bytes() != payload
            for relative, payload in expected.items()
        ):
            raise RuntimeError("UAT_ERROR_RETEST_INPUT_IMMUTABLE_MISMATCH")
    else:
        for relative, payload in expected.items():
            _atomic_write(package_root / relative, payload)
    plan = {
        "revision": "uat-future-error-retest-plan:v4",
        "input_manifest_ref": "uat-future-error-retest-v4/manifest.json",
        "input_manifest_sha256": _sha256(package_root / "manifest.json"),
        "preflight_blocked_ref": "uat-future-error-retest-v3/preflight-blocked.json",
        "preflight_blocked_sha256": hashlib.sha256(
            blocked_payload, usedforsecurity=False
        ).hexdigest(),
        "selected_case_count": len(selected_ids),
        "eligible_case_count": len(eligible),
        "blocked_case_count": len(blocked),
        "max_provider_requests": 15,
        "per_case_max_requests": 1,
        "automatic_retries": 0,
        "runner": {
            "revision": "uat-future-error-retest-runner:v3",
            "checkpoint_ref": "provider-checkpoints/uat-future-error-retest-v3.json",
            "result_ref": "uat-claim-results/error-retest-v3",
            "audit_ref": "uat-claim-audits/error-retest-v3",
            "coverage_ref": "uat-claim-audits/error-retest-v3/coverage.json",
            "approved_by_user": False,
            "executed": False,
        },
        "historical_artifacts": "READ_ONLY",
        "provider_call_count": 0,
        "network_call_count": 0,
        "zilliz_write_count": 0,
        "content_output": False,
    }
    _atomic_write(OUTPUT_PLAN, _json_bytes(plan, indent=2))
    print(
        json.dumps(
            {
                "selected_case_count": len(selected_ids),
                "eligible_case_count": len(eligible),
                "blocked_case_count": len(blocked),
                "max_provider_requests": 15,
                "provider_call_count": 0,
                "network_call_count": 0,
                "executed": False,
                "content_output": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
