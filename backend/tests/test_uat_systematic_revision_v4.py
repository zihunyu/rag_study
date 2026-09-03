from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from ragkb.config import load_env
from ragkb.evaluation.uat_systematic_revision import (
    build_systematic_revision_v4,
    distinctive_positive_terms_v4,
)
from ragkb.infrastructure.uat_artifacts import LocalUatArtifactStore


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _sources(root: Path):
    loaded = load_env(root)
    assert loaded.settings is not None
    artifacts_root = loaded.settings.local_storage_artifacts_dir
    if not artifacts_root.is_absolute():
        artifacts_root = (root / artifacts_root).resolve()
    plan = json.loads(
        (root / "artifacts/final-validation/uat-continuation-v3-plan.json").read_text(
            encoding="utf-8"
        )
    )
    records = plan["selected_bundles"][3:]
    bundles = [
        json.loads((artifacts_root / record["bundle_ref"]).read_text(encoding="utf-8"))
        for record in records
    ]
    return bundles, records


def test_systematic_v4_is_deterministic_positive_only_and_preserves_all_evidence(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    bundles, records = _sources(root)
    first = build_systematic_revision_v4(bundles, records)
    second = build_systematic_revision_v4(bundles, records)
    assert first == second
    review, revised_bundles, manifest = first
    assert review["candidate_count"] == len(review["revisions"]) == 75
    assert review["status"] == "PENDING_USER_REVIEW"
    assert len(revised_bundles) == 75
    assert review["network_call_performed"] is False
    for original, revision in zip(bundles, review["revisions"], strict=True):
        revised = revised_bundles[revision["revision_candidate_id"]]
        assert revised["documents"] == original["documents"]
        assert revised["expected_positive_evidence_id"] == original["expected_positive_evidence_id"]
        assert revised["question"] == revision["revised_question"]
        assert revised["question"] != original["question"]
        assert revision["terms_from_positive_only"] is True
        assert revision["evidence_external_facts_added"] is False
        positive = next(
            document for document in original["documents"] if document["role"] == "positive"
        )
        distractors = [
            document["content"]
            for document in original["documents"]
            if document["role"] == "distractor"
        ]
        allowed = {
            _hash(term) for term in distinctive_positive_terms_v4(positive["content"], distractors)
        }
        assert set(revision["local_term_sha256"]).issubset(allowed)
    store = LocalUatArtifactStore(tmp_path)
    stored_first = store.persist_systematic_revision_v4(review, revised_bundles, manifest)
    stored_second = store.persist_systematic_revision_v4(review, revised_bundles, manifest)
    assert stored_first == stored_second
    assert stored_first["bundle_count"] == 75


def test_systematic_v4_fails_whole_set_when_one_bundle_has_no_distinctive_term() -> None:
    root = Path(__file__).resolve().parents[2]
    bundles, records = _sources(root)
    invalid = copy.deepcopy(bundles)
    shared = invalid[0]["documents"][0]["content"]
    shared_hash = hashlib.sha256(shared.encode()).hexdigest()
    for document in invalid[0]["documents"]:
        document["content"] = shared
        document["content_sha256"] = shared_hash
    with pytest.raises(ValueError, match="DISTINCTIVE_TERMS_MISSING"):
        build_systematic_revision_v4(invalid, records)


def test_v3_failure_keeps_full_rank_and_old_checkpoint_hash() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "artifacts/final-validation/provider-checkpoints/uat-reranker-v3.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "72a2fdf766891a8414bc6a77848828d41d3bf96274fcb82094b55f209ce4b30e"
    )
    namespace = json.loads(path.read_text(encoding="utf-8"))["uat_reranker_v3"]
    failed = [
        value
        for key, value in namespace.items()
        if key != "_manifest" and value.get("state") == "FAILED"
    ]
    assert len(failed) == 1
    assert failed[0]["positive_rank"] == 4
    assert failed[0]["response_index_count"] == 4
    assert len(failed[0]["ranked_evidence_ids"]) == 4
    assert failed[0]["automatic_retries"] == 0
