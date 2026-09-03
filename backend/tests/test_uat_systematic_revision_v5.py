from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from ragkb.config import load_env
from ragkb.evaluation.uat_systematic_revision import distinctive_positive_terms_v4
from ragkb.evaluation.uat_systematic_revision_v5 import build_systematic_revision_v5
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore


def _term_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _sources(root: Path):
    loaded = load_env(root)
    assert loaded.settings is not None
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (root / artifacts_root).resolve()
    plan = json.loads(
        (root / "artifacts/final-validation/uat-systematic-v4-execution-plan.json").read_text(
            encoding="utf-8"
        )
    )
    records = plan["selected_bundles"][39:]
    bundles = [
        json.loads((artifacts_root / record["bundle_ref"]).read_text(encoding="utf-8"))
        for record in records
    ]
    return bundles, records


def test_systematic_v5_is_deterministic_two_term_positive_only_and_evidence_unchanged(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    bundles, records = _sources(root)
    first = build_systematic_revision_v5(bundles, records)
    second = build_systematic_revision_v5(bundles, records)
    assert first == second
    review, revised_bundles, manifest = first
    assert review["candidate_count"] == len(review["revisions"]) == 39
    assert review["status"] == "PENDING_USER_REVIEW"
    assert len(revised_bundles) == 39
    for source, revision in zip(bundles, review["revisions"], strict=True):
        revised = revised_bundles[revision["revision_candidate_id"]]
        assert revised["documents"] == source["documents"]
        assert revised["expected_positive_evidence_id"] == source["expected_positive_evidence_id"]
        assert revised["question"] == revision["revised_question"]
        assert revised["question"] != source["question"]
        assert len(revision["local_term_sha256"]) == 2
        assert len(set(revision["local_term_sha256"])) == 2
        assert revision["terms_from_positive_only"] is True
        assert revision["evidence_external_facts_added"] is False
        positive = next(
            document for document in source["documents"] if document["role"] == "positive"
        )
        distractors = [
            document["content"]
            for document in source["documents"]
            if document["role"] == "distractor"
        ]
        allowed = {
            _term_hash(term)
            for term in distinctive_positive_terms_v4(positive["content"], distractors, limit=64)
        }
        assert set(revision["local_term_sha256"]).issubset(allowed)
    store = LocalUatArtifactStore(tmp_path)
    stored = store.persist_systematic_revision_v5(review, revised_bundles, manifest)
    assert store.persist_systematic_revision_v5(review, revised_bundles, manifest) == stored
    assert stored["bundle_count"] == 39


def test_systematic_v5_fails_whole_set_if_two_terms_are_unavailable() -> None:
    root = Path(__file__).resolve().parents[2]
    bundles, records = _sources(root)
    invalid = copy.deepcopy(bundles)
    shared = invalid[0]["documents"][0]["content"]
    shared_hash = hashlib.sha256(shared.encode()).hexdigest()
    for document in invalid[0]["documents"]:
        document["content"] = shared
        document["content_sha256"] = shared_hash
    with pytest.raises(ValueError, match="TWO_DISTINCTIVE_TERMS_MISSING"):
        build_systematic_revision_v5(invalid, records)


def test_v4_failure_rank_and_checkpoint_are_frozen() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "artifacts/final-validation/provider-checkpoints/uat-reranker-v4.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "e024ec8029370125116db5180f5ea0f12d699c388aa76ba3e4ded5cd294b901a"
    )
    namespace = json.loads(path.read_text(encoding="utf-8"))["uat_reranker_v4"]
    failed = [
        value
        for key, value in namespace.items()
        if key != "_manifest" and value.get("state") == "FAILED"
    ]
    assert len(failed) == 1
    assert failed[0]["candidate_id"] == "8b6b08e289b402b1f741"
    assert failed[0]["positive_rank"] == 3
    assert failed[0]["response_index_count"] == 4
    assert len(failed[0]["ranked_evidence_ids"]) == 4
    assert failed[0]["automatic_retries"] == 0
