from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from ragkb.application.uat_provider_runners import (
    UatRerankerSystematicV5Runner,
    UatSystematicLlmV4ExecutionRunner,
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
            "answer": "synthetic v5 answer",
            "citation_ids": [item["evidence_id"] for item in evidence],
        }


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(position: int) -> str:
    if position == 1:
        return "v1"
    if position == 2:
        return "v2"
    if position == 3:
        return "v3"
    return "v4" if position <= 39 else "v5"


def _bundles(root: Path) -> list[dict[str, object]]:
    loaded = load_env(root)
    assert loaded.settings is not None
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (root / artifacts_root).resolve()
    plan = json.loads(
        (root / "artifacts/final-validation/uat-systematic-revision-v5-plan.json").read_text(
            encoding="utf-8"
        )
    )
    records = plan["selected_bundles"]
    assert len(records) == 78
    return [
        json.loads((artifacts_root / record["bundle_ref"]).read_text(encoding="utf-8"))
        for record in records
    ]


def _gate(root: Path, bundles: list[dict[str, object]], v5: JsonCheckpointStore):
    paths = {
        "v1": root / "artifacts/final-validation/provider-checkpoints/uat-reranker-v1.json",
        "v2": root / "artifacts/final-validation/provider-checkpoints/uat-reranker-v2.json",
        "v3": root / "artifacts/final-validation/provider-checkpoints/uat-reranker-v3.json",
        "v4": root / "artifacts/final-validation/provider-checkpoints/uat-reranker-v4.json",
    }
    stores = {source: JsonCheckpointStore(path) for source, path in paths.items()}
    namespaces = {
        "v1": "uat_reranker",
        "v2": "uat_reranker_v2",
        "v3": "uat_reranker_v3",
        "v4": "uat_reranker_v4",
    }
    records: dict[str, dict[str, object]] = {}
    provenance: dict[str, str] = {}
    for position, bundle in enumerate(bundles, start=1):
        source = _source(position)
        candidate_id = str(bundle["candidate_id"])
        record = (
            v5.get("uat_reranker_v5", candidate_id)
            if source == "v5"
            else stores[source].get(namespaces[source], candidate_id)
        )
        assert record is not None
        records[candidate_id] = record
        provenance[candidate_id] = source
    return build_combined_reranker_gate(
        bundles,
        records,
        provenance,
        {**{source: _hash(path) for source, path in paths.items()}, "v5": _hash(v5.path)},
        revision="uat-combined-reranker-gate:v5",
        required_sources=frozenset({"v1", "v2", "v3", "v4", "v5"}),
    )


def test_v5_39_combines_existing_39_then_llm_v4_78_with_safe_resume(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    bundles = _bundles(root)
    v5 = JsonCheckpointStore(tmp_path / "v5.json")
    reranker = _Reranker()
    result = UatRerankerSystematicV5Runner(reranker, v5, external_call_approved=False).run(
        bundles[39:]
    )
    assert result["request_count"] == result["completed_count"] == 39
    assert result["combined_gate_ready"] is True
    assert len(reranker.calls) == 39
    UatRerankerSystematicV5Runner(reranker, v5, external_call_approved=False).run(bundles[39:])
    assert len(reranker.calls) == 39

    gate = _gate(root, bundles, v5)
    assert gate["candidate_count"] == gate["gate_passed_count"] == 78
    assert gate["revision"] == "uat-combined-reranker-gate:v5"
    assert gate["source_checkpoint_hashes"].keys() == {"v1", "v2", "v3", "v4", "v5"}

    llm = _Llm()
    llm_checkpoints = JsonCheckpointStore(tmp_path / "llm-v4.json")
    results = LocalUatArtifactStore(tmp_path / "results", result_revision="v4")
    llm_result = UatSystematicLlmV4ExecutionRunner(
        llm, llm_checkpoints, results, external_call_approved=False
    ).run(bundles, gate)
    assert llm_result["request_count"] == llm_result["completed_count"] == 78
    assert llm_result["citation_gate_passed_count"] == 78
    assert llm_result["user_result_review_required"] is True
    assert llm_result["real_uat_passed"] is False
    assert len(llm.calls) == 78
    UatSystematicLlmV4ExecutionRunner(
        llm, llm_checkpoints, results, external_call_approved=False
    ).run(bundles, gate)
    assert len(llm.calls) == 78
    assert len(list(results.result_root.glob("*.json"))) == 78
    serialized = v5.path.read_text(encoding="utf-8") + llm_checkpoints.path.read_text(
        encoding="utf-8"
    )
    assert '"question"' not in serialized
    assert '"content"' not in serialized
    assert "synthetic v5 answer" not in serialized


def test_v5_first_failure_prevents_combined_gate_and_llm(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    bundles = _bundles(root)
    v5 = JsonCheckpointStore(tmp_path / "failed-v5.json")
    reranker = _Reranker(fail_at=0)
    with pytest.raises(ProviderExecutionError, match="POSITIVE_NOT_IN_TOP_K"):
        UatRerankerSystematicV5Runner(reranker, v5, external_call_approved=False).run(bundles[39:])
    assert len(reranker.calls) == 1
    mutated = copy.deepcopy(bundles[39:])
    mutated[0]["question"] = str(mutated[0]["question"]) + " changed"
    with pytest.raises(ProviderExecutionError, match="SNAPSHOT_OR_PARAMETERS_MISMATCH"):
        UatRerankerSystematicV5Runner(reranker, v5, external_call_approved=False).run(mutated)
    assert len(reranker.calls) == 1

    llm = _Llm()
    with pytest.raises(ProviderExecutionError, match="GLOBAL_GATE_NOT_MET"):
        UatSystematicLlmV4ExecutionRunner(
            llm,
            JsonCheckpointStore(tmp_path / "must-not-call-llm.json"),
            LocalUatArtifactStore(tmp_path / "must-not-store", result_revision="v4"),
            external_call_approved=False,
        ).run(
            bundles,
            {
                "revision": "uat-combined-reranker-gate:v5",
                "candidate_count": 78,
                "gate_passed_count": 77,
                "positive_top_k": 2,
                "results": [],
                "source_checkpoint_hashes": {
                    "v1": "a" * 64,
                    "v2": "b" * 64,
                    "v3": "c" * 64,
                    "v4": "d" * 64,
                    "v5": "e" * 64,
                },
                "llm_execution_unlocked": False,
            },
        )
    assert llm.calls == []
