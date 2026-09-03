from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from ragkb.application.uat_provider_runners import (
    UatLlmExecutionRunner,
    UatRerankerExecutionRunner,
)
from ragkb.config import load_env
from ragkb.contracts.provider_execution import ProviderExecutionError
from ragkb.evaluation.real_uat import build_uat_bundles
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore


class _Reranker:
    real_network = False

    def __init__(self, *, fail_gate: bool = False) -> None:
        self.fail_gate = fail_gate
        self.calls: list[str] = []

    def rerank(self, query, documents, top_n, idempotency_key, timeout_seconds):
        del query, timeout_seconds
        self.calls.append(idempotency_key)
        order = list(range(len(documents)))
        return order[1:] + order[:1] if self.fail_gate else order[:top_n]


class _Llm:
    real_network = False

    def __init__(self, *, invalid_citation: bool = False) -> None:
        self.invalid_citation = invalid_citation
        self.calls: list[str] = []

    def generate(self, question, evidence, idempotency_key, timeout_seconds):
        del question, timeout_seconds
        self.calls.append(idempotency_key)
        citation = "not-in-bundle" if self.invalid_citation else evidence[0]["evidence_id"]
        return {
            "status": "answered",
            "answer": "synthetic reviewed answer",
            "citation_ids": [citation],
        }


def _actual_bundles(root: Path):
    plan = build_uat_bundles(root)
    settings = load_env(root).settings
    assert settings is not None
    artifacts_root = settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (root / artifacts_root).resolve()
    store = LocalUatArtifactStore(artifacts_root, bundle_revision="v2")
    bundles = [store.read_bundle(str(item["candidate_id"])) for item in plan["bundles"]]
    return plan, bundles


def test_78_locator_grounded_bundles_have_one_positive_three_safe_distractors() -> None:
    root = Path(__file__).resolve().parents[2]
    plan, bundles = _actual_bundles(root)
    assert plan["bundle_count"] == 78
    assert plan["documents_per_bundle"] == 4
    assert plan["reranker"]["max_requests"] == 78
    assert plan["llm"]["max_requests"] == 78
    assert plan["total_model_request_budget"] == 156
    assert plan["candidate2_reranker_diagnostic_v2"]["checkpoint_ref"].endswith(
        "uat-reranker-v2.json"
    )
    assert plan["candidate2_reranker_diagnostic_v2"]["executed"] is True
    assert plan["remaining_reranker_continuation_v3"]["approved_by_user"] is True
    assert plan["remaining_reranker_continuation_v3"]["executed"] is True
    assert plan["remaining_reranker_continuation_v3"]["llm_request_count"] == 0
    assert plan["query_embedding_request_count"] == 0
    assert plan["zilliz_request_count"] == 0
    assert plan["conditional_user_authorization_satisfied"] is True
    for bundle in bundles:
        documents = bundle["documents"]
        positives = [document for document in documents if document["role"] == "positive"]
        distractors = [document for document in documents if document["role"] == "distractor"]
        assert len(positives) == 1 and len(distractors) == 3
        assert all(
            document["anonymous_sample_id"] != positives[0]["anonymous_sample_id"]
            for document in distractors
        )
        assert all(
            document["source_category"] == bundle["source_category"] for document in documents
        )
        assert all(
            hashlib.sha256(document["content"].encode()).hexdigest() == document["content_sha256"]
            for document in documents
        )
    serialized_plan = json.dumps(plan)
    assert '"content":' not in serialized_plan
    assert '"question":' not in serialized_plan


def test_uat_reranker_and_llm_fake_78_each_resume_without_duplicate(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    _, bundles = _actual_bundles(root)
    reranker_checkpoint = JsonCheckpointStore(tmp_path / "uat-reranker.json")
    reranker = _Reranker()
    rerank_result = UatRerankerExecutionRunner(
        reranker,
        reranker_checkpoint,
        external_call_approved=False,
        max_requests=78,
        positive_top_k=2,
    ).run(bundles)
    assert rerank_result["request_count"] == rerank_result["completed_count"] == 78
    assert rerank_result["llm_execution_unlocked"] is True
    assert len(reranker.calls) == 78
    UatRerankerExecutionRunner(
        reranker,
        reranker_checkpoint,
        external_call_approved=False,
    ).run(bundles)
    assert len(reranker.calls) == 78

    llm_checkpoint = JsonCheckpointStore(tmp_path / "uat-llm.json")
    llm = _Llm()
    result_store = LocalUatArtifactStore(tmp_path / "results")
    llm_result = UatLlmExecutionRunner(
        llm,
        reranker_checkpoint,
        llm_checkpoint,
        result_store,
        external_call_approved=False,
        max_requests=78,
        reranker_top_k=2,
    ).run(bundles)
    assert llm_result["request_count"] == llm_result["completed_count"] == 78
    assert llm_result["citation_gate_passed_count"] == 78
    assert llm_result["real_uat_passed"] is False
    assert llm_result["user_result_review_required"] is True
    assert len(llm.calls) == 78
    UatLlmExecutionRunner(
        llm,
        reranker_checkpoint,
        llm_checkpoint,
        result_store,
        external_call_approved=False,
    ).run(bundles)
    assert len(llm.calls) == 78
    rerank_serialized = reranker_checkpoint.path.read_text(encoding="utf-8")
    llm_serialized = llm_checkpoint.path.read_text(encoding="utf-8")
    assert '"question"' not in rerank_serialized + llm_serialized
    assert '"content"' not in rerank_serialized + llm_serialized
    assert "synthetic reviewed answer" not in llm_serialized


def test_reranker_gate_blocks_all_llm_and_invalid_citation_stops(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    _, bundles = _actual_bundles(root)
    failed_reranker_cp = JsonCheckpointStore(tmp_path / "failed-reranker.json")
    with pytest.raises(ProviderExecutionError, match="POSITIVE_NOT_IN_TOP_K"):
        UatRerankerExecutionRunner(
            _Reranker(fail_gate=True),
            failed_reranker_cp,
            external_call_approved=False,
        ).run(bundles)
    failed_records = [
        value
        for key, value in json.loads(failed_reranker_cp.path.read_text(encoding="utf-8"))[
            "uat_reranker"
        ].items()
        if key != "_manifest"
    ]
    assert len(failed_records) == 1
    assert failed_records[0]["state"] == "FAILED"
    assert failed_records[0]["gate_passed"] is False
    assert failed_records[0]["positive_rank"] == 4
    assert failed_records[0]["response_index_count"] == 4
    assert len(failed_records[0]["ranked_evidence_ids"]) == 4
    failed_serialized = failed_reranker_cp.path.read_text(encoding="utf-8")
    assert '"question"' not in failed_serialized
    assert '"content"' not in failed_serialized
    llm = _Llm()
    with pytest.raises(ProviderExecutionError, match="RERANKER_GLOBAL_GATE_NOT_MET"):
        UatLlmExecutionRunner(
            llm,
            failed_reranker_cp,
            JsonCheckpointStore(tmp_path / "must-not-call-llm.json"),
            LocalUatArtifactStore(tmp_path / "must-not-store"),
            external_call_approved=False,
        ).run(bundles)
    assert llm.calls == []

    good_reranker_cp = JsonCheckpointStore(tmp_path / "good-reranker.json")
    UatRerankerExecutionRunner(_Reranker(), good_reranker_cp, external_call_approved=False).run(
        bundles
    )
    invalid_llm = _Llm(invalid_citation=True)
    invalid_llm_cp = JsonCheckpointStore(tmp_path / "invalid-citation.json")
    with pytest.raises(ProviderExecutionError, match="CITATION_GATE_FAILED"):
        UatLlmExecutionRunner(
            invalid_llm,
            good_reranker_cp,
            invalid_llm_cp,
            LocalUatArtifactStore(tmp_path / "invalid-results"),
            external_call_approved=False,
        ).run(bundles)
    assert len(invalid_llm.calls) == 1
    assert "synthetic reviewed answer" not in invalid_llm_cp.path.read_text(encoding="utf-8")


def test_reranker_rejects_bundle_and_manifest_mutations_before_new_request(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    _, bundles = _actual_bundles(root)
    checkpoint_store = JsonCheckpointStore(tmp_path / "reranker-binding.json")
    transport = _Reranker()
    runner = UatRerankerExecutionRunner(transport, checkpoint_store, external_call_approved=False)
    runner.run(bundles)
    assert len(transport.calls) == 78

    changed_question = copy.deepcopy(bundles)
    changed_question[0]["question"] += " changed"
    with pytest.raises(
        ProviderExecutionError, match="UAT_RERANKER_SNAPSHOT_OR_PARAMETERS_MISMATCH"
    ):
        runner.run(changed_question)
    assert len(transport.calls) == 78

    manifest = checkpoint_store.get("uat_reranker", "_manifest")
    assert manifest is not None
    manifest["positive_top_k"] = 3
    checkpoint_store.save("uat_reranker", "_manifest", manifest)
    with pytest.raises(
        ProviderExecutionError, match="UAT_RERANKER_SNAPSHOT_OR_PARAMETERS_MISMATCH"
    ):
        runner.run(bundles)
    assert len(transport.calls) == 78

    invalid_cases = []
    changed_positive = copy.deepcopy(bundles)
    changed_positive[0]["expected_positive_evidence_id"] = changed_positive[0]["documents"][1][
        "evidence_id"
    ]
    invalid_cases.append(changed_positive)
    changed_id = copy.deepcopy(bundles)
    changed_id[0]["documents"][0]["evidence_id"] = changed_id[0]["documents"][1]["evidence_id"]
    invalid_cases.append(changed_id)
    changed_content = copy.deepcopy(bundles)
    changed_content[0]["documents"][0]["content"] += " changed"
    invalid_cases.append(changed_content)
    changed_locator = copy.deepcopy(bundles)
    changed_locator[0]["documents"][0]["locator"] = {"unsafe": "value"}
    invalid_cases.append(changed_locator)

    for index, invalid_bundles in enumerate(invalid_cases):
        invalid_transport = _Reranker()
        with pytest.raises(ProviderExecutionError, match="UAT_BUNDLE_"):
            UatRerankerExecutionRunner(
                invalid_transport,
                JsonCheckpointStore(tmp_path / f"invalid-bundle-{index}.json"),
                external_call_approved=False,
            ).run(invalid_bundles)
        assert invalid_transport.calls == []


def test_llm_resume_binds_reranker_order_and_manifest_parameters(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    _, bundles = _actual_bundles(root)
    reranker_checkpoint = JsonCheckpointStore(tmp_path / "reranker.json")
    UatRerankerExecutionRunner(_Reranker(), reranker_checkpoint, external_call_approved=False).run(
        bundles
    )
    llm_checkpoint = JsonCheckpointStore(tmp_path / "llm-binding.json")
    llm = _Llm()
    runner = UatLlmExecutionRunner(
        llm,
        reranker_checkpoint,
        llm_checkpoint,
        LocalUatArtifactStore(tmp_path / "llm-results"),
        external_call_approved=False,
    )
    runner.run(bundles)
    assert len(llm.calls) == 78

    candidate_id = str(bundles[0]["candidate_id"])
    reranker_result = reranker_checkpoint.get("uat_reranker", candidate_id)
    assert reranker_result is not None
    ranked = list(reranker_result["ranked_evidence_ids"])
    ranked[0], ranked[1] = ranked[1], ranked[0]
    reranker_result["ranked_evidence_ids"] = ranked
    positive_id = str(reranker_result["expected_positive_evidence_id"])
    reranker_result["positive_rank"] = ranked.index(positive_id) + 1
    reranker_checkpoint.save("uat_reranker", candidate_id, reranker_result)
    with pytest.raises(ProviderExecutionError, match="UAT_LLM_SNAPSHOT_OR_PARAMETERS_MISMATCH"):
        runner.run(bundles)
    assert len(llm.calls) == 78

    ranked[0], ranked[1] = ranked[1], ranked[0]
    reranker_result["ranked_evidence_ids"] = ranked
    reranker_result["positive_rank"] = ranked.index(positive_id) + 1
    reranker_checkpoint.save("uat_reranker", candidate_id, reranker_result)
    manifest = llm_checkpoint.get("uat_llm", "_manifest")
    assert manifest is not None
    manifest["max_requests"] = 77
    llm_checkpoint.save("uat_llm", "_manifest", manifest)
    with pytest.raises(ProviderExecutionError, match="UAT_LLM_SNAPSHOT_OR_PARAMETERS_MISMATCH"):
        runner.run(bundles)
    assert len(llm.calls) == 78
