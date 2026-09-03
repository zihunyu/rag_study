from __future__ import annotations

import hashlib

from ragkb.evaluation.uat_revision_proposals import (
    build_revision_proposals,
    distinctive_positive_terms,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _review() -> dict[str, object]:
    return {
        "candidate_id": "0123456789abcdefabcd",
        "question": "这个项目的验收标准是什么？",
        "documents": [
            {
                "role": "positive",
                "content": "星河验收需要蓝色凭证和OrchidProtocol记录。API_KEY=do-not-use。",
            },
            {"role": "distractor", "content": "其他项目使用普通凭证和日常记录。"},
            {"role": "distractor", "content": "常规验收流程不包含特别术语。"},
            {"role": "distractor", "content": "项目资料采用通用归档要求。"},
        ],
    }


def test_revision_proposals_are_deterministic_and_positive_grounded() -> None:
    review = _review()
    first = build_revision_proposals(review)
    second = build_revision_proposals(review)
    assert first == second
    assert first["proposal_count"] == 3
    assert first["status"] == "PENDING_USER_REVIEW"
    assert first["network_call_performed"] is False
    documents = review["documents"]
    assert isinstance(documents, list)
    positive = str(documents[0]["content"])
    distractors = [str(document["content"]) for document in documents[1:]]
    allowed_hashes = {_hash(term) for term in distinctive_positive_terms(positive, distractors)}
    for proposal in first["proposals"]:
        assert set(proposal["local_term_sha256"]).issubset(allowed_hashes)
        assert proposal["terms_from_positive_only"] is True
        assert proposal["evidence_external_facts_added"] is False
        assert "API_KEY" not in proposal["question"]
        assert "do-not-use" not in proposal["question"]


def test_distinctive_terms_support_cjk_and_ascii_and_exclude_noise() -> None:
    terms = distinctive_positive_terms(
        "量子蓝图 OrchidProtocol 123456 token password",
        ["普通蓝图", "日常文档", "常规记录"],
        limit=20,
    )
    assert "OrchidProtocol".casefold() in terms
    assert any("量子" in term for term in terms)
    assert not any(term.isdigit() for term in terms)
    assert "token" not in terms
    assert "password" not in terms
