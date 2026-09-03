from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from ragkb.application.uat_provider_runners import (
    UatRerankerSystematicV4Runner,
    UatSystematicLlmV3ExecutionRunner,
    build_combined_reranker_gate,
)
from ragkb.config import load_env
from ragkb.contracts.provider_execution import ProviderExecutionError
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


def _bundles(root: Path) -> list[dict[str, object]]:
    loaded = load_env(root)
    assert loaded.settings is not None
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (root / artifacts_root).resolve()
    plan = json.loads(
        (root / "artifacts/final-validation/uat-systematic-revision-v4-plan.json").read_text(
            encoding="utf-8"
        )
    )
    return [
        json.loads((artifacts_root / record["bundle_ref"]).read_text(encoding="utf-8"))
        for record in plan["selected_bundles"]
    ]


def _gate(root: Path, bundles: list[dict[str, object]], v4: JsonCheckpointStore):
    paths = {
        "v1": root / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json",
        "v2": root / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json",
        "v3": root / "artifacts/final-validation/provider-checkpoints/uat-reranker-v3.json",
    }
    stores = {key: JsonCheckpointStore(path) for key, path in paths.items()}
    namespaces = {"v1": "uat_reranker", "v2": "uat_reranker_v2", "v3": "uat_reranker_v3"}
    records = {}
    provenance = {}
    for position, bundle in enumerate(bundles, start=1):
        source = (
            "v1" if position == 1 else "v2" if position == 2 else "v3" if position == 3 else "v4"
        )
        candidate_id = str(bundle["candidate_id"])
        record = (
            v4.get("uat_reranker_v4", candidate_id)
            if source == "v4"
            else stores[source].get(namespaces[source], candidate_id)
        )
        assert record is not None
        records[candidate_id] = record
        provenance[candidate_id] = source
    return build_combined_reranker_gate(
        bundles,
        records,
        provenance,
        {**{key: _hash(path) for key, path in paths.items()}, "v4": _hash(v4.path)},
        revision="uat-combined-reranker-gate:v4",
        required_sources=frozenset({"v1", "v2", "v3", "v4"}),
    )


def test_v4_75_combines_78_then_llm_v3_78_with_no_duplicate(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    bundles = _bundles(root)
    v4 = JsonCheckpointStore(tmp_path / "v4.json")
    reranker = _Reranker()
    result = UatRerankerSystematicV4Runner(reranker, v4, external_call_approved=False).run(
        bundles[3:]
    )
    assert result["request_count"] == result["completed_count"] == 75
    assert len(reranker.calls) == 75
    UatRerankerSystematicV4Runner(reranker, v4, external_call_approved=False).run(bundles[3:])
    assert len(reranker.calls) == 75
    gate = _gate(root, bundles, v4)
    assert gate["candidate_count"] == gate["gate_passed_count"] == 78
    assert gate["revision"] == "uat-combined-reranker-gate:v4"

    llm = _Llm()
    llm_cp = JsonCheckpointStore(tmp_path / "llm-v3.json")
    results = LocalUatArtifactStore(tmp_path / "results", result_revision="v3")
    llm_result = UatSystematicLlmV3ExecutionRunner(
        llm, llm_cp, results, external_call_approved=False
    ).run(bundles, gate)
    assert llm_result["request_count"] == llm_result["completed_count"] == 78
    assert llm_result["citation_gate_passed_count"] == 78
    assert llm_result["real_uat_passed"] is False
    assert len(llm.calls) == 78
    UatSystematicLlmV3ExecutionRunner(llm, llm_cp, results, external_call_approved=False).run(
        bundles, gate
    )
    assert len(llm.calls) == 78
    assert len(list(results.result_root.glob("*.json"))) == 78
    serialized = v4.path.read_text(encoding="utf-8") + llm_cp.path.read_text(encoding="utf-8")
    assert '"question"' not in serialized
    assert '"content"' not in serialized
    assert "synthetic answer" not in serialized


def test_v4_first_failure_blocks_combined_and_llm(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    bundles = _bundles(root)
    v4 = JsonCheckpointStore(tmp_path / "failed-v4.json")
    reranker = _Reranker(fail_at=0)
    with pytest.raises(ProviderExecutionError, match="POSITIVE_NOT_IN_TOP_K"):
        UatRerankerSystematicV4Runner(reranker, v4, external_call_approved=False).run(bundles[3:])
    assert len(reranker.calls) == 1
    mutated = copy.deepcopy(bundles[3:])
    mutated[0]["question"] = str(mutated[0]["question"]) + " changed"
    with pytest.raises(ProviderExecutionError, match="SNAPSHOT_OR_PARAMETERS_MISMATCH"):
        UatRerankerSystematicV4Runner(reranker, v4, external_call_approved=False).run(mutated)
    assert len(reranker.calls) == 1
    llm = _Llm()
    with pytest.raises(ProviderExecutionError, match="GLOBAL_GATE_NOT_MET"):
        UatSystematicLlmV3ExecutionRunner(
            llm,
            JsonCheckpointStore(tmp_path / "must-not-call-llm.json"),
            LocalUatArtifactStore(tmp_path / "must-not-store", result_revision="v3"),
            external_call_approved=False,
        ).run(
            bundles,
            {
                "revision": "uat-combined-reranker-gate:v4",
                "candidate_count": 78,
                "gate_passed_count": 77,
                "positive_top_k": 2,
                "results": [],
                "source_checkpoint_hashes": {
                    "v1": "a" * 64,
                    "v2": "b" * 64,
                    "v3": "c" * 64,
                    "v4": "d" * 64,
                },
                "llm_execution_unlocked": False,
            },
        )
    assert llm.calls == []
