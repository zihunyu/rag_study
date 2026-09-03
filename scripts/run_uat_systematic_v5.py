"""Plan or execute v5 Reranker and the conditional LLM v4 UAT stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping
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
    UatRerankerSystematicV5Runner,
    UatSystematicLlmV4ExecutionRunner,
    build_combined_reranker_gate,
)
from ragkb.config import EnvSettings, load_env  # noqa: E402
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore  # noqa: E402
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore  # noqa: E402

PLAN_PATH = ROOT / "artifacts/final-validation/uat-systematic-v5-execution-plan.json"
SOURCE_PLAN = ROOT / "artifacts/final-validation/uat-systematic-revision-v5-plan.json"
V1 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json"
V2 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json"
V3 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v3.json"
V4 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v4.json"
V5 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v5.json"
LLM_V4 = ROOT / "artifacts/final-validation/provider-checkpoints/uat-llm-v4.json"
COMBINED = ROOT / "artifacts/final-validation/uat-combined-reranker-gate-v5.json"


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


def _expected_source(position: int) -> str:
    if position == 1:
        return "v1"
    if position == 2:
        return "v2"
    if position == 3:
        return "v3"
    return "v4" if position <= 39 else "v5"


def _source_paths() -> dict[str, Path]:
    return {"v1": V1, "v2": V2, "v3": V3, "v4": V4, "v5": V5}


def _source_namespaces() -> dict[str, str]:
    return {
        "v1": "uat_reranker",
        "v2": "uat_reranker_v2",
        "v3": "uat_reranker_v3",
        "v4": "uat_reranker_v4",
        "v5": "uat_reranker_v5",
    }


def _required_configured(loaded_configured: Mapping[str, bool]) -> bool:
    required = (
        "RERANKER_BASE_URL",
        "RERANKER_API_KEY",
        "RERANKER_MODEL",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "AI_APPROVED_PROCESSING_REGIONS",
    )
    return all(loaded_configured.get(key, False) for key in required)


def _load_plan() -> dict[str, object]:
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise RuntimeError("UAT_SYSTEMATIC_V5_EXECUTION_PLAN_INVALID")
    return plan


def _context() -> tuple[
    EnvSettings, LocalUatArtifactStore, dict[str, object], list[dict[str, object]]
]:
    loaded = load_env(ROOT)
    if loaded.settings is None:
        raise RuntimeError("CONFIG_INVALID")
    if not _required_configured(loaded.configured):
        raise RuntimeError("UAT_SYSTEMATIC_V5_REQUIRED_CONFIG_MISSING")
    settings = loaded.settings
    artifacts_root = settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    store = LocalUatArtifactStore(artifacts_root, bundle_revision="v2", result_revision="v4")
    plan = _load_plan()
    reranker = plan.get("reranker_v5")
    llm = plan.get("llm_v4")
    records = plan.get("selected_bundles")
    expected_hashes = {key: _sha256(path) for key, path in _source_paths().items() if key != "v5"}
    if (
        plan.get("revision") != "uat-systematic-v5-execution-plan:v1"
        or plan.get("approved_by_user") is not True
        or plan.get("authorization_scope")
        != "SYSTEMATIC_V5_39_RERANKER_THEN_CONDITIONAL_78_LLM_RETRY_ZERO"
        or plan.get("source_revision_plan_sha256") != _sha256(SOURCE_PLAN)
        or not isinstance(reranker, Mapping)
        or reranker.get("candidate_count") != 39
        or reranker.get("max_requests") != 39
        or reranker.get("positive_top_k") != 2
        or reranker.get("automatic_retries") != 0
        or reranker.get("approved_by_user") is not True
        or not isinstance(llm, Mapping)
        or llm.get("candidate_count") != 78
        or llm.get("max_requests") != 78
        or llm.get("reranker_top_k") != 2
        or llm.get("automatic_retries") != 0
        or llm.get("approved_by_user") is not True
        or llm.get("user_result_review_required") is not True
        or not isinstance(records, list)
        or len(records) != 78
        or plan.get("selected_bundle_count") != 78
        or plan.get("selected_bundle_snapshot_sha256") != _canonical_hash(records)
        or plan.get("source_checkpoint_hashes") != expected_hashes
    ):
        raise RuntimeError("UAT_SYSTEMATIC_V5_EXECUTION_PLAN_INVALID")
    review_path = artifacts_root / "uat-systematic-revision-v5/approved-review.json"
    manifest_path = artifacts_root / "uat-systematic-revision-v5/manifest.json"
    if _sha256(review_path) != plan.get("source_review_sha256") or _sha256(
        manifest_path
    ) != plan.get("source_manifest_sha256"):
        raise RuntimeError("UAT_SYSTEMATIC_V5_REVIEW_HASH_MISMATCH")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("revision") != "uat-systematic-revision-manifest:v5"
        or manifest.get("candidate_count") != 39
        or manifest.get("bundle_count") != 39
        or manifest.get("bundle_snapshot_sha256") != plan.get("v5_revision_bundle_snapshot_sha256")
        or manifest.get("source_checkpoint_hashes") != expected_hashes
        or manifest.get("passed_source_counts") != {"v1": 1, "v2": 1, "v3": 1, "v4": 36}
    ):
        raise RuntimeError("UAT_SYSTEMATIC_V5_MANIFEST_INVALID")
    bundles: list[dict[str, object]] = []
    for position, record in enumerate(records, start=1):
        source = _expected_source(position)
        if (
            not isinstance(record, dict)
            or record.get("position") != position
            or record.get("source_checkpoint") != source
        ):
            raise RuntimeError("UAT_SYSTEMATIC_V5_BUNDLE_RECORD_INVALID")
        if position >= 40 and not isinstance(record.get("source_revision_candidate_id"), str):
            raise RuntimeError("UAT_SYSTEMATIC_V5_REVISION_PROVENANCE_INVALID")
        path = (artifacts_root / str(record.get("bundle_ref", ""))).resolve()
        if artifacts_root not in path.parents or _sha256(path) != record.get("bundle_sha256"):
            raise RuntimeError("UAT_SYSTEMATIC_V5_BUNDLE_HASH_MISMATCH")
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(bundle, dict) or bundle.get("candidate_id") != record.get("candidate_id"):
            raise RuntimeError("UAT_SYSTEMATIC_V5_BUNDLE_INVALID")
        bundles.append(bundle)
    _validate_existing_passes(bundles)
    require_configured_provider_egress(
        outbound_ai_allowed=settings.ai_outbound_allowed,
        allowed_classifications=settings.ai_outbound_allowed_classifications,
        approved_processing_regions=settings.ai_approved_processing_regions,
        classifications=[str(bundle["source_classification"]) for bundle in bundles],
    )
    return settings, store, plan, bundles


def _validate_existing_passes(bundles: list[dict[str, object]]) -> None:
    paths = _source_paths()
    namespaces = _source_namespaces()
    for position, bundle in enumerate(bundles[:39], start=1):
        source = _expected_source(position)
        checkpoint = JsonCheckpointStore(paths[source]).get(
            namespaces[source], str(bundle["candidate_id"])
        )
        if (
            checkpoint is None
            or checkpoint.get("state") != "COMPLETED"
            or checkpoint.get("gate_passed") is not True
        ):
            raise RuntimeError("UAT_SYSTEMATIC_V5_EXISTING_PASS_PROVENANCE_INVALID")


def _record(store: JsonCheckpointStore, namespace: str, candidate_id: str) -> dict[str, object]:
    record = store.get(namespace, candidate_id)
    if record is None:
        raise RuntimeError("UAT_SYSTEMATIC_V5_RERANKER_RECORD_MISSING")
    return record


def _build_gate(bundles: list[dict[str, object]]) -> tuple[dict[str, object], str]:
    paths = _source_paths()
    namespaces = _source_namespaces()
    stores = {source: JsonCheckpointStore(path) for source, path in paths.items()}
    records: dict[str, dict[str, object]] = {}
    provenance: dict[str, str] = {}
    for position, bundle in enumerate(bundles, start=1):
        source = _expected_source(position)
        candidate_id = str(bundle["candidate_id"])
        records[candidate_id] = _record(stores[source], namespaces[source], candidate_id)
        provenance[candidate_id] = source
    gate = build_combined_reranker_gate(
        bundles,
        records,
        provenance,
        {source: _sha256(path) for source, path in paths.items()},
        revision="uat-combined-reranker-gate:v5",
        required_sources=frozenset({"v1", "v2", "v3", "v4", "v5"}),
    )
    _atomic_json(COMBINED, gate)
    return gate, _sha256(COMBINED)


def _assert_llm_recovery_consistent(store: LocalUatArtifactStore) -> None:
    if not LLM_V4.exists():
        if store.result_root.exists() and any(store.result_root.iterdir()):
            raise RuntimeError("UAT_SYSTEMATIC_V5_LLM_RECOVERY_INCONSISTENT")
        return
    payload = json.loads(LLM_V4.read_text(encoding="utf-8"))
    namespace = payload.get("uat_llm_v4") if isinstance(payload, dict) else None
    if not isinstance(namespace, dict):
        raise RuntimeError("UAT_SYSTEMATIC_V5_LLM_RECOVERY_INCONSISTENT")
    checkpoints = {
        candidate_id: value
        for candidate_id, value in namespace.items()
        if candidate_id != "_manifest" and isinstance(value, dict)
    }
    files = list(store.result_root.iterdir()) if store.result_root.exists() else []
    for path in files:
        candidate_id = path.stem
        checkpoint = checkpoints.get(candidate_id)
        if (
            path.suffix != ".json"
            or len(candidate_id) != 20
            or any(character not in "0123456789abcdef" for character in candidate_id)
            or checkpoint is None
            or checkpoint.get("state") != "COMPLETED"
            or checkpoint.get("result_ref") != f"uat-results/v4/{candidate_id}.json"
            or checkpoint.get("result_sha256") != _sha256(path)
        ):
            raise RuntimeError("UAT_SYSTEMATIC_V5_LLM_RECOVERY_INCONSISTENT")
    for candidate_id, checkpoint in checkpoints.items():
        if checkpoint.get("state") == "COMPLETED":
            path = store.result_root / f"{candidate_id}.json"
            if not path.is_file() or checkpoint.get("result_sha256") != _sha256(path):
                raise RuntimeError("UAT_SYSTEMATIC_V5_LLM_RECOVERY_INCONSISTENT")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "reranker", "llm"), default="plan", nargs="?")
    parser.add_argument("--approved", action="store_true")
    args = parser.parse_args()
    plan = _load_plan()
    if args.mode == "plan":
        print(
            json.dumps(
                {
                    "revision": plan.get("revision"),
                    "approved_by_user": plan.get("approved_by_user"),
                    "existing_passed_count": 39,
                    "reranker_v5_count": plan.get("reranker_v5", {}).get("candidate_count"),
                    "reranker_v5_max_requests": plan.get("reranker_v5", {}).get("max_requests"),
                    "combined_gate_required_count": plan.get("combined_gate_v5", {}).get(
                        "required_count"
                    ),
                    "llm_v4_conditional_count": plan.get("llm_v4", {}).get("candidate_count"),
                    "llm_v4_max_requests": plan.get("llm_v4", {}).get("max_requests"),
                    "automatic_retries": 0,
                    "reranker_checkpoint_exists": V5.exists(),
                    "combined_gate_exists": COMBINED.exists(),
                    "llm_checkpoint_exists": LLM_V4.exists(),
                    "content_output": False,
                },
                sort_keys=True,
            )
        )
        return 0
    if not args.approved:
        raise RuntimeError("UAT_SYSTEMATIC_V5_EXECUTION_APPROVAL_REQUIRED")
    settings, store, _, bundles = _context()
    if args.mode == "reranker":
        if COMBINED.exists():
            raise RuntimeError("UAT_RERANKER_V5_COMBINED_GATE_ALREADY_EXISTS")
        result = UatRerankerSystematicV5Runner(
            UatRerankerHttpTransport(settings),
            JsonCheckpointStore(V5),
            external_call_approved=True,
            max_requests=39,
            positive_top_k=2,
            timeout_seconds=settings.reranker_timeout_seconds,
        ).run(bundles[39:])
        gate, gate_hash = _build_gate(bundles)
        result = {
            **result,
            "combined_gate_passed_count": gate["gate_passed_count"],
            "combined_gate_sha256": gate_hash,
            "llm_execution_unlocked": True,
        }
    else:
        if not V5.is_file() or not COMBINED.is_file():
            raise RuntimeError("UAT_SYSTEMATIC_V5_COMBINED_GATE_NOT_READY")
        _assert_llm_recovery_consistent(store)
        gate = json.loads(COMBINED.read_text(encoding="utf-8"))
        expected_hashes = {source: _sha256(path) for source, path in _source_paths().items()}
        if not isinstance(gate, dict) or gate.get("source_checkpoint_hashes") != expected_hashes:
            raise RuntimeError("UAT_SYSTEMATIC_V5_COMBINED_GATE_HASH_MISMATCH")
        result = UatSystematicLlmV4ExecutionRunner(
            UatLlmHttpTransport(settings),
            JsonCheckpointStore(LLM_V4),
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
