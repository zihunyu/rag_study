from __future__ import annotations

from ragkb.adapters.mysql_entity_store import EntityRow, MySQLNormalizedEntityStore
from ragkb.adapters.mysql_governance import MySQLGovernanceRepository, _empty
from ragkb.adapters.mysql_lifecycle import MySQLLifecycleStore
from ragkb.adapters.mysql_upload import MySQLUploadRepository, _empty_state


class _Cursor:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.rowcount = 1

    def execute(self, statement: str, parameters: object = None) -> None:
        del parameters
        self.statements.append(" ".join(statement.split()))


def test_normalized_store_updates_only_the_changed_entity_row() -> None:
    store = MySQLNormalizedEntityStore("upload_entities_v3", "tenant")
    before = {
        ("documents", "a"): EntityRow("a", None, 0, {"value": 1}, 3),
        ("documents", "b"): EntityRow("b", None, 0, {"value": 1}, 5),
    }
    after = {
        ("documents", "a"): EntityRow("a", None, 0, {"value": 1}),
        ("documents", "b"): EntityRow("b", None, 0, {"value": 2}),
    }
    cursor = _Cursor()

    store.sync(cursor, before, after)

    assert len(cursor.statements) == 1
    assert "UPDATE upload_entities_v3" in cursor.statements[0]


def test_upload_governance_and_lifecycle_codecs_round_trip_per_entity() -> None:
    upload = _empty_state()
    upload["documents"]["doc"] = {"id": "doc", "row_version": 1}
    upload["versions"]["version"] = {
        "id": "version",
        "document_id": "doc",
        "version_no": 1,
    }
    upload["reviews"]["version"] = [{"review_id": "review", "decision": "APPROVED"}]
    assert (
        MySQLUploadRepository._from_entities(MySQLUploadRepository._to_entities(upload)) == upload
    )

    governance = _empty()
    governance["events"].append({"event_id": "event", "severity": "info"})
    governance["pilots"]["pilot"] = {"pilot_id": "pilot", "revision": 1}
    assert (
        MySQLGovernanceRepository._from_entities(MySQLGovernanceRepository._to_entities(governance))
        == governance
    )

    lifecycle = {
        "documents": [
            {
                "document_id": "doc",
                "active_version_id": "version",
                "version_history": [],
                "lifecycle_state": "ACTIVE",
                "acl_revision": 1,
                "visible": True,
                "tombstoned": False,
                "row_version": 2,
            }
        ],
        "transitions": [],
        "tombstones": [],
        "audit_events": [],
        "processed_events": ["event"],
        "idempotency": [],
    }
    assert (
        MySQLLifecycleStore._from_entities(MySQLLifecycleStore._to_entities(lifecycle)) == lifecycle
    )
