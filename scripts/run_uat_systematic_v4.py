"""Plan or execute systematic Reranker v4 and conditional LLM v3."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.provider_http import (  # noqa: E402
    UatLlmHttpTransport,
    UatRerankerHttpTransport,
)
from ragkb.application.provider_runners import (  # noqa: E402
    require_configured_provider_egress,
)
from ragkb.application.uat_provider_runners import (  # noqa: E402
    UatRerankerSystematicV4Runner,
    UatSystematicLlmV3ExecutionRunner,
    build_combined_reranker_gate,
)
from ragkb.config import EnvSettings, load_env  # noqa: E402
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore  # noqa: E402
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore  # noqa: E402

PLAN_PATH = ROOT / "artifacts/final-validation/uat-systematic-v4-execution-plan.json"
V1 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json"
V2 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json"
V3 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v3.json"
V4 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v4.json"
LLM_V3 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-llm-v3.json"
COMBINED = ROOT / "artifacts/final-validation/uat-combined-reranker-gate-v4.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes(), usedforsecurity=False).hexdigest()


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode(),
        usedforsecurity=False,
    ).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
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


def _context() -> tuple[
    EnvSettings, LocalUatArtifactStore, dict[str, object], list[dict[str, object]]
]:
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    required = (
        "RERANKER_BASE_URL",
        "RERANKER_API_KEY",
        "RERANKER_MODEL",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "AI_APPROVED_PROCESSING_REGIONS",
    )
    if not all(loaded.configured.get(key, False) for key in required):
        raise RuntimeError("UAT_SYSTEMATIC_V4_REQUIRED_CONFIG_MISSING")
    settings = loaded.settings
    artifacts_root = settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    store = LocalUatArtifactStore(artifacts_root, bundle_revision="v2", result_revision="v3")
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    reranker = plan.get("reranker_v4")
    llm = plan.get("llm_v3")
    records = plan.get("selected_bundles")
    if (
        plan.get("revision") != "uat-systematic-v4-execution-plan:v1"
        or plan.get("approved_by_user") is not True
        or plan.get("authorization_scope")
        != "SYSTEMATIC_V4_75_RERANKER_THEN_CONDITIONAL_78_LLM_RETRY_ZERO"
        or not isinstance(reranker, dict)
        or reranker.get("candidate_count") != 75
        or reranker.get("max_requests") != 75
        or reranker.get("positive_top_k") != 2
        or reranker.get("automatic_retries") != 0
        or reranker.get("approved_by_user") is not True
        or not isinstance(llm, dict)
        or llm.get("candidate_count") != 78
        or llm.get("max_requests") != 78
        or llm.get("automatic_retries") != 0
        or llm.get("approved_by_user") is not True
        or not isinstance(records, list)
        or len(records) != 78
        or plan.get("selected_bundle_snapshot_sha256") != _canonical_hash(records)
    ):
        raise RuntimeError("UAT_SYSTEMATIC_V4_EXECUTION_PLAN_INVALID")
    review_path = artifacts_root / "uat-systematic-revision-v4/approved-review.json"
    manifest_path = artifacts_root / "uat-systematic-revision-v4/manifest.json"
    if _sha256(review_path) != plan.get("source_review_sha256") or _sha256(
        manifest_path
    ) != plan.get("source_manifest_sha256"):
        raise RuntimeError("UAT_SYSTEMATIC_V4_REVIEW_HASH_MISMATCH")
    checkpoint_hashes = plan.get("source_checkpoint_hashes")
    if not isinstance(checkpoint_hashes, dict) or checkpoint_hashes != {
        "v1": _sha256(V1),
        "v2": _sha256(V2),
        "v3": _sha256(V3),
    }:
        raise RuntimeError("UAT_SYSTEMATIC_V4_CHECKPOINT_HASH_MISMATCH")
    bundles = []
    for position, record in enumerate(records, start=1):
        expected_source = (
            "v1" if position == 1 else "v2" if position == 2 else "v3" if position == 3 else "v4"
        )
        if (
            not isinstance(record, dict)
            or record.get("position") != position
            or record.get("source_checkpoint") != expected_source
        ):
            raise RuntimeError("UAT_SYSTEMATIC_V4_BUNDLE_RECORD_INVALID")
        path = (artifacts_root / str(record["bundle_ref"])).resolve()
        if artifacts_root not in path.parents or _sha256(path) != record.get("bundle_sha256"):
            raise RuntimeError("UAT_SYSTEMATIC_V4_BUNDLE_HASH_MISMATCH")
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(bundle, dict) or bundle.get("candidate_id") != record.get("candidate_id"):
            raise RuntimeError("UAT_SYSTEMATIC_V4_BUNDLE_INVALID")
        bundles.append(bundle)
    require_configured_provider_egress(
        outbound_ai_allowed=settings.ai_outbound_allowed,
        allowed_classifications=settings.ai_outbound_allowed_classifications,
        approved_processing_regions=settings.ai_approved_processing_regions,
        classifications=[str(bundle["source_classification"]) for bundle in bundles],
    )
    return settings, store, plan, bundles


def _record(store: JsonCheckpointStore, namespace: str, candidate_id: str) -> dict[str, object]:
    record = store.get(namespace, candidate_id)
    if record is None:
        raise RuntimeError("UAT_SYSTEMATIC_V4_RERANKER_RECORD_MISSING")
    return record


def _build_gate(bundles: list[dict[str, object]]) -> tuple[dict[str, object], str]:
    stores = {
        "v1": JsonCheckpointStore(V1),
        "v2": JsonCheckpointStore(V2),
        "v3": JsonCheckpointStore(V3),
        "v4": JsonCheckpointStore(V4),
    }
    namespaces = {
        "v1": "uat_reranker",
        "v2": "uat_reranker_v2",
        "v3": "uat_reranker_v3",
        "v4": "uat_reranker_v4",
    }
    records = {}
    provenance = {}
    for position, bundle in enumerate(bundles, start=1):
        source = (
            "v1" if position == 1 else "v2" if position == 2 else "v3" if position == 3 else "v4"
        )
        candidate_id = str(bundle["candidate_id"])
        records[candidate_id] = _record(stores[source], namespaces[source], candidate_id)
        provenance[candidate_id] = source
    gate = build_combined_reranker_gate(
        bundles,
        records,
        provenance,
        {
            source: _sha256(path)
            for source, path in {"v1": V1, "v2": V2, "v3": V3, "v4": V4}.items()
        },
        revision="uat-combined-reranker-gate:v4",
        required_sources=frozenset({"v1", "v2", "v3", "v4"}),
    )
    _atomic_json(COMBINED, gate)
    return gate, _sha256(COMBINED)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "reranker", "llm"), default="plan", nargs="?")
    parser.add_argument("--approved", action="store_true")
    args = parser.parse_args()
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    if args.mode == "plan":
        print(
            json.dumps(
                {
                    "revision": plan["revision"],
                    "approved_by_user": plan["approved_by_user"],
                    "selected_bundle_count": plan["selected_bundle_count"],
                    "reranker_v4_count": plan["reranker_v4"]["candidate_count"],
                    "reranker_v4_max_requests": plan["reranker_v4"]["max_requests"],
                    "llm_v3_conditional_count": plan["llm_v3"]["candidate_count"],
                    "llm_v3_max_requests": plan["llm_v3"]["max_requests"],
                    "automatic_retries": 0,
                    "reranker_checkpoint_exists": V4.exists(),
                    "combined_gate_exists": COMBINED.exists(),
                    "llm_checkpoint_exists": LLM_V3.exists(),
                    "content_output": False,
                },
                sort_keys=True,
            )
        )
        return 0
    if not args.approved:
        raise RuntimeError("UAT_SYSTEMATIC_V4_EXECUTION_APPROVAL_REQUIRED")
    settings, store, _, bundles = _context()
    if args.mode == "reranker":
        if V4.exists() or COMBINED.exists():
            raise RuntimeError("UAT_RERANKER_V4_EXECUTION_ARTIFACT_ALREADY_EXISTS")
        result = UatRerankerSystematicV4Runner(
            UatRerankerHttpTransport(settings),
            JsonCheckpointStore(V4),
            external_call_approved=True,
            max_requests=75,
            positive_top_k=2,
            timeout_seconds=settings.reranker_timeout_seconds,
        ).run(bundles[3:])
        gate, gate_hash = _build_gate(bundles)
        result = {
            **result,
            "combined_gate_passed_count": gate["gate_passed_count"],
            "combined_gate_sha256": gate_hash,
            "llm_execution_unlocked": True,
        }
    else:
        if not V4.is_file() or not COMBINED.is_file():
            raise RuntimeError("UAT_SYSTEMATIC_V4_COMBINED_GATE_NOT_READY")
        if LLM_V3.exists() or (store.result_root.exists() and any(store.result_root.iterdir())):
            raise RuntimeError("UAT_LLM_V3_EXECUTION_ARTIFACT_ALREADY_EXISTS")
        gate = json.loads(COMBINED.read_text(encoding="utf-8"))
        source_hashes = gate.get("source_checkpoint_hashes")
        expected_hashes = {
            "v1": _sha256(V1),
            "v2": _sha256(V2),
            "v3": _sha256(V3),
            "v4": _sha256(V4),
        }
        if not isinstance(source_hashes, dict) or source_hashes != expected_hashes:
            raise RuntimeError("UAT_SYSTEMATIC_V4_COMBINED_GATE_HASH_MISMATCH")
        result = UatSystematicLlmV3ExecutionRunner(
            UatLlmHttpTransport(settings),
            JsonCheckpointStore(LLM_V3),
            store,
            external_call_approved=True,
            max_requests=78,
            reranker_top_k=2,
            timeout_seconds=settings.llm_timeout_seconds,
        ).run(bundles, gate)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
