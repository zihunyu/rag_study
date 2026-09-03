from __future__ import annotations

from ragkb.evaluation.uat_error_case_retest import (
    prepare_retest_cases,
    select_retest_case_ids,
)
from ragkb.evaluation.uat_generic_remediation import canonical_sha256, text_sha256


def _source(
    seed: int, *, corrupted: bool = False, rendered_proof: bool = True
) -> dict[str, object]:
    content = f"field-{seed}=value-{seed}"
    locator = {"page": seed + 1}
    if corrupted:
        content = f"field-{seed}\x00value-{seed}"
    return {
        "question": f"request-{seed}",
        "allow_cross_document": False,
        "source_classification": "confidential",
        "evidence": {
            "evidence_id": f"evidence-{seed}",
            "source_document_id": f"document-{seed}",
            "source_version_sha256": text_sha256(f"version-{seed}"),
            "content": content,
            "rendered_text": content if rendered_proof else None,
            "render_proof": {
                "revision": "uat-independent-render-proof:v1",
                "source_version_sha256": text_sha256(f"version-{seed}"),
                "locator_sha256": canonical_sha256(locator),
                "representation_sha256": text_sha256(content),
            },
            "requires_rendered_proof": True,
            "locator": locator,
            "entity_id": None,
            "field_key": None,
        },
    }


def test_dynamic_retest_selection_and_preflight_isolates_blocked_cases() -> None:
    rows = [
        {"type": "candidate_review", "audit_verdict": "不通过", "candidate_id": "scope-a"},
        {"type": "candidate_review", "audit_verdict": "待修订", "candidate_id": "scope-b"},
        {"type": "candidate_review", "audit_verdict": "通过", "candidate_id": "scope-c"},
        {"type": "audit_metadata", "audit_verdict": "不通过", "candidate_id": "scope-d"},
    ]
    selected = select_retest_case_ids(rows)
    assert selected == ["scope-a", "scope-b"]
    eligible, blocked, manifest = prepare_retest_cases(
        selected,
        {"scope-a": _source(1), "scope-b": _source(2, corrupted=True)},
    )
    assert len(eligible) == 1
    assert len(blocked) == 1
    assert blocked[0]["state"] == "BLOCKED"
    assert blocked[0]["provider_call_count"] == 0
    assert manifest["selected_case_count"] == 2
    assert manifest["eligible_case_count"] == 1
    assert manifest["blocked_case_count"] == 1
    assert "answer" not in eligible[0]


def test_missing_render_proof_is_blocked_without_provider_budget() -> None:
    eligible, blocked, manifest = prepare_retest_cases(
        ["scope-render"], {"scope-render": _source(3, rendered_proof=False)}
    )
    assert eligible == []
    assert blocked[0]["state"] == "BLOCKED"
    assert blocked[0]["provider_call_count"] == 0
    assert manifest["blocked_case_count"] == 1
