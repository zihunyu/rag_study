from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from ragkb.application.uat_provider_runners import (
    UatCombinedLlmExecutionRunner,
    UatRerankerContinuationV3Runner,
    build_combined_reranker_gate,
)
from ragkb.config import load_env
from ragkb.contracts.provider_execution import ProviderExecutionError
from ragkb.evaluation.real_uat import build_uat_bundles
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore


class _Reranker:
    real_network = False

    def __init__(self, *, fail_at: int | None = None) -> None:
        self.fail_at = fail_at
        self.calls: list[str] = []

    def rerank(self, query, documents, top_n, idempotency_key, timeout_seconds):
        del query, top_n, timeout_seconds
        position = len(self.calls)
        self.calls.append(idempotency_key)
        order = list(range(len(documents)))
        return order[1:] + order[:1] if position == self.fail_at else order


class _Llm:
    real_network = False

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, question, evidence, idempotency_key, timeout_seconds):
        del question, timeout_seconds
        self.calls.append(idempotency_key)
        return {
            "status": "answered",
            "answer": "synthetic answer",
            "citation_ids": [evidence[0]["evidence_id"]],
        }


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _final_bundles(root: Path) -> list[dict[str, object]]:
    plan = build_uat_bundles(root)
    loaded = load_env(root)
    assert loaded.settings is not None
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (root / artifacts_root).resolve()
    store = LocalUatArtifactStore(artifacts_root, bundle_revision="v2")
    original = [store.read_bundle(str(item["candidate_id"])) for item in plan["bundles"]]
    diagnostic_plan = json.loads(
        (root / "artifacts/final-validation/uat-reranker-diagnostic-v2-plan.json").read_text(
            encoding="utf-8"
        )
    )
    diagnostic = store.read_diagnostic_bundle_v2(str(diagnostic_plan["bundle"]["candidate_id"]))
    return [original[0], diagnostic, *original[2:]]


def _combined_gate(
    root: Path,
    bundles: list[dict[str, object]],
    v3: JsonCheckpointStore,
) -> dict[str, object]:
    v1_path = root / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json"
    v2_path = root / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json"
    v1 = JsonCheckpointStore(v1_path)
    v2 = JsonCheckpointStore(v2_path)
    records = {}
    provenance = {}
    first_id = str(bundles[0]["candidate_id"])
    second_id = str(bundles[1]["candidate_id"])
    first = v1.get("uat_reranker", first_id)
    second = v2.get("uat_reranker_v2", second_id)
    assert first is not None and second is not None
    records[first_id] = first
    records[second_id] = second
    provenance[first_id] = "v1"
    provenance[second_id] = "v2"
    for bundle in bundles[2:]:
        candidate_id = str(bundle["candidate_id"])
        record = v3.get("uat_reranker_v3", candidate_id)
        assert record is not None
        records[candidate_id] = record
        provenance[candidate_id] = "v3"
    return build_combined_reranker_gate(
        bundles,
        records,
        provenance,
        {"v1": _hash(v1_path), "v2": _hash(v2_path), "v3": _hash(v3.path)},
    )


def test_remaining_76_then_combined_78_and_llm_78_resume(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    bundles = _final_bundles(root)
    v3 = JsonCheckpointStore(tmp_path / "v3.json")
    reranker = _Reranker()
    rerank_result = UatRerankerContinuationV3Runner(reranker, v3, external_call_approved=False).run(
        bundles[2:]
    )
    assert rerank_result["request_count"] == rerank_result["completed_count"] == 76
    assert rerank_result["gate_passed_count"] == 76
    assert len(reranker.calls) == 76
    UatRerankerContinuationV3Runner(reranker, v3, external_call_approved=False).run(bundles[2:])
    assert len(reranker.calls) == 76
    gate = _combined_gate(root, bundles, v3)
    assert gate["candidate_count"] == gate["gate_passed_count"] == 78
    assert gate["llm_execution_unlocked"] is True

    llm_cp = JsonCheckpointStore(tmp_path / "llm-v2.json")
    llm = _Llm()
    result_store = LocalUatArtifactStore(
        tmp_path / "results", bundle_revision="v2", result_revision="v2"
    )
    llm_result = UatCombinedLlmExecutionRunner(
        llm, llm_cp, result_store, external_call_approved=False
    ).run(bundles, gate)
    assert llm_result["request_count"] == llm_result["completed_count"] == 78
    assert llm_result["citation_gate_passed_count"] == 78
    assert llm_result["real_uat_passed"] is False
    assert len(llm.calls) == 78
    UatCombinedLlmExecutionRunner(llm, llm_cp, result_store, external_call_approved=False).run(
        bundles, gate
    )
    assert len(llm.calls) == 78
    assert len(list(result_store.result_root.glob("*.json"))) == 78
    serialized = v3.path.read_text(encoding="utf-8") + llm_cp.path.read_text(encoding="utf-8")
    assert '"question"' not in serialized
    assert '"content"' not in serialized
    assert "synthetic answer" not in serialized


def test_remaining_failure_blocks_combined_gate_and_all_llm(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    bundles = _final_bundles(root)
    v3 = JsonCheckpointStore(tmp_path / "failed-v3.json")
    reranker = _Reranker(fail_at=0)
    with pytest.raises(ProviderExecutionError, match="POSITIVE_NOT_IN_TOP_K"):
        UatRerankerContinuationV3Runner(reranker, v3, external_call_approved=False).run(bundles[2:])
    assert len(reranker.calls) == 1
    llm = _Llm()
    invalid_gate = {
        "revision": "uat-combined-reranker-gate:v3",
        "candidate_count": 78,
        "gate_passed_count": 77,
        "positive_top_k": 2,
        "results": [],
        "source_checkpoint_hashes": {"v1": "a" * 64, "v2": "b" * 64, "v3": "c" * 64},
        "llm_execution_unlocked": False,
    }
    with pytest.raises(ProviderExecutionError, match="GLOBAL_GATE_NOT_MET"):
        UatCombinedLlmExecutionRunner(
            llm,
            JsonCheckpointStore(tmp_path / "must-not-call-llm.json"),
            LocalUatArtifactStore(tmp_path / "must-not-store", result_revision="v2"),
            external_call_approved=False,
        ).run(bundles, invalid_gate)
    assert llm.calls == []

    mutated = copy.deepcopy(bundles[2:])
    mutated[0]["question"] = str(mutated[0]["question"]) + " changed"
    with pytest.raises(ProviderExecutionError, match="SNAPSHOT_OR_PARAMETERS_MISMATCH"):
        UatRerankerContinuationV3Runner(reranker, v3, external_call_approved=False).run(mutated)
    assert len(reranker.calls) == 1
