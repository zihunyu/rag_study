"""Plan or execute remaining-76 Reranker and conditional-78 LLM UAT."""

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
    UatCombinedLlmExecutionRunner,
    UatRerankerContinuationV3Runner,
    build_combined_reranker_gate,
)
from ragkb.config import EnvSettings, load_env  # noqa: E402
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore  # noqa: E402
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore  # noqa: E402

PLAN_PATH = ROOT / "artifacts/final-validation/uat-continuation-v3-plan.json"
V1_PATH = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json"
V2_PATH = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json"
V3_PATH = ROOT / "artifacts/final-validation/provider-checkpoints/uat-reranker-v3.json"
LLM_V2_PATH = ROOT / "artifacts/final-validation/provider-checkpoints/uat-llm-v2.json"
GATE_PATH = ROOT / "artifacts/final-validation/uat-combined-reranker-gate-v3.json"


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
        raise RuntimeError("UAT_CONTINUATION_REQUIRED_CONFIG_MISSING")
    settings = loaded.settings
    artifacts_root = settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (ROOT / artifacts_root).resolve()
    store = LocalUatArtifactStore(artifacts_root, bundle_revision="v2", result_revision="v2")
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    reranker = plan.get("reranker_v3")
    llm = plan.get("llm_v2")
    records = plan.get("selected_bundles")
    if (
        plan.get("revision") != "uat-continuation-plan:v3"
        or plan.get("approved_by_user") is not True
        or plan.get("authorization_scope")
        != "REMAINING_76_RERANKER_THEN_CONDITIONAL_78_LLM_RETRY_ZERO"
        or not isinstance(reranker, dict)
        or reranker.get("remaining_candidate_count") != 76
        or reranker.get("max_requests") != 76
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
        or plan.get("selected_bundle_snapshot_hash") != _canonical_hash(records)
    ):
        raise RuntimeError("UAT_CONTINUATION_PLAN_INVALID")
    source_hashes = plan.get("source_checkpoint_hashes")
    if (
        not isinstance(source_hashes, dict)
        or _sha256(V1_PATH) != source_hashes.get("v1")
        or _sha256(V2_PATH) != source_hashes.get("v2")
    ):
        raise RuntimeError("UAT_CONTINUATION_PRIOR_CHECKPOINT_HASH_MISMATCH")
    bundles: list[dict[str, object]] = []
    for position, record in enumerate(records, start=1):
        expected_source = "v1" if position == 1 else "v2" if position == 2 else "v3"
        if (
            not isinstance(record, dict)
            or record.get("position") != position
            or record.get("source_checkpoint") != expected_source
        ):
            raise RuntimeError("UAT_CONTINUATION_BUNDLE_RECORD_INVALID")
        path = (artifacts_root / str(record.get("bundle_ref"))).resolve()
        if artifacts_root not in path.parents or _sha256(path) != record.get("bundle_sha256"):
            raise RuntimeError("UAT_CONTINUATION_BUNDLE_HASH_MISMATCH")
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(bundle, dict) or bundle.get("candidate_id") != record.get("candidate_id"):
            raise RuntimeError("UAT_CONTINUATION_BUNDLE_INVALID")
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
        raise RuntimeError("UAT_COMBINED_RERANKER_RECORD_MISSING")
    return record


def _build_and_store_gate(bundles: list[dict[str, object]]) -> tuple[dict[str, object], str]:
    v1 = JsonCheckpointStore(V1_PATH)
    v2 = JsonCheckpointStore(V2_PATH)
    v3 = JsonCheckpointStore(V3_PATH)
    records: dict[str, dict[str, object]] = {}
    provenance: dict[str, str] = {}
    first_id = str(bundles[0]["candidate_id"])
    second_id = str(bundles[1]["candidate_id"])
    records[first_id] = _record(v1, "uat_reranker", first_id)
    provenance[first_id] = "v1"
    records[second_id] = _record(v2, "uat_reranker_v2", second_id)
    provenance[second_id] = "v2"
    for bundle in bundles[2:]:
        candidate_id = str(bundle["candidate_id"])
        records[candidate_id] = _record(v3, "uat_reranker_v3", candidate_id)
        provenance[candidate_id] = "v3"
    gate = build_combined_reranker_gate(
        bundles,
        records,
        provenance,
        {"v1": _sha256(V1_PATH), "v2": _sha256(V2_PATH), "v3": _sha256(V3_PATH)},
    )
    _atomic_json(GATE_PATH, gate)
    return gate, _sha256(GATE_PATH)


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
                    "remaining_reranker_count": plan["reranker_v3"]["remaining_candidate_count"],
                    "reranker_max_requests": plan["reranker_v3"]["max_requests"],
                    "llm_conditional_count": plan["llm_v2"]["candidate_count"],
                    "llm_max_requests": plan["llm_v2"]["max_requests"],
                    "automatic_retries": 0,
                    "reranker_checkpoint_exists": V3_PATH.exists(),
                    "combined_gate_exists": GATE_PATH.exists(),
                    "llm_checkpoint_exists": LLM_V2_PATH.exists(),
                    "content_output": False,
                },
                sort_keys=True,
            )
        )
        return 0
    if not args.approved:
        raise RuntimeError("UAT_CONTINUATION_EXECUTION_APPROVAL_REQUIRED")
    settings, store, _, bundles = _context()
    if args.mode == "reranker":
        if V3_PATH.exists() or GATE_PATH.exists():
            raise RuntimeError("UAT_RERANKER_V3_EXECUTION_ARTIFACT_ALREADY_EXISTS")
        result = UatRerankerContinuationV3Runner(
            UatRerankerHttpTransport(settings),
            JsonCheckpointStore(V3_PATH),
            external_call_approved=True,
            max_requests=76,
            positive_top_k=2,
            timeout_seconds=settings.reranker_timeout_seconds,
        ).run(bundles[2:])
        gate, gate_hash = _build_and_store_gate(bundles)
        result = {
            **result,
            "combined_gate_passed_count": gate["gate_passed_count"],
            "combined_gate_sha256": gate_hash,
            "llm_execution_unlocked": True,
        }
    else:
        if not V3_PATH.is_file() or not GATE_PATH.is_file():
            raise RuntimeError("UAT_COMBINED_RERANKER_GATE_NOT_READY")
        if LLM_V2_PATH.exists() or (
            store.result_root.exists() and any(store.result_root.iterdir())
        ):
            raise RuntimeError("UAT_LLM_V2_EXECUTION_ARTIFACT_ALREADY_EXISTS")
        gate = json.loads(GATE_PATH.read_text(encoding="utf-8"))
        source_hashes = gate.get("source_checkpoint_hashes")
        if not isinstance(source_hashes, dict) or source_hashes != {
            "v1": _sha256(V1_PATH),
            "v2": _sha256(V2_PATH),
            "v3": _sha256(V3_PATH),
        }:
            raise RuntimeError("UAT_COMBINED_RERANKER_GATE_HASH_MISMATCH")
        result = UatCombinedLlmExecutionRunner(
            UatLlmHttpTransport(settings),
            JsonCheckpointStore(LLM_V2_PATH),
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
