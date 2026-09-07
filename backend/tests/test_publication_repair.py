import pytest
from ragkb.infrastructure.publication_repair import recovery_decision


def evidence():
    snapshot = {
        "document_id": "d",
        "active_version_id": "v",
        "lifecycle_state": "SWITCHING",
        "visible": False,
        "tombstoned": False,
        "version_history": [],
        "row_version": 2,
        "acl_revision": 1,
    }
    record = {**snapshot, "lifecycle_state": "DRAFT", "row_version": 1}
    return {
        "intent": {"snapshot": snapshot},
        "record": record,
        "checks": {"all_evidence_matches": True},
        "startup_recovery_only": True,
        "later_lifecycle_operation": False,
        "tombstone": False,
    }


def test_only_recovery_draft_is_repaired_and_rerun_is_noop():
    item = evidence()
    decision, reasons, target = recovery_decision(item)
    assert decision == "repairable" and not reasons
    assert target["lifecycle_state"] == "ACTIVE" and target["visible"]
    item["record"] = target
    assert recovery_decision(item) == ("already_consistent", [], None)


@pytest.mark.parametrize(
    "change",
    [
        {"later_lifecycle_operation": True},
        {"tombstone": True},
        {"startup_recovery_only": False},
        {"checks": {"向量记录不一致": False}},
        {"checks": {}},
    ],
)
def test_inconsistent_or_later_actions_prevent_resurrection(change):
    item = evidence()
    item.update(change)
    assert recovery_decision(item)[0] == "blocked"
    assert recovery_decision(item)[2] is None


@pytest.mark.parametrize("state", ["REVOKED", "DELETED", "SECURITY_TRANSITION", "SWITCHING"])
def test_non_recovery_lifecycle_state_is_never_overwritten(state):
    item = evidence()
    item["record"]["lifecycle_state"] = state
    assert recovery_decision(item)[0] == "blocked"


def test_newer_draft_and_prior_version_history_prevent_repair():
    item = evidence()
    item["record"]["row_version"] = 3
    assert recovery_decision(item)[0] == "blocked"
    item = evidence()
    item["record"]["version_history"] = ["older"]
    assert recovery_decision(item)[0] == "blocked"
