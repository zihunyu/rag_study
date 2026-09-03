from __future__ import annotations

from copy import deepcopy

from ragkb.application.lifecycle import InMemoryLifecycleStore, LifecycleService
from ragkb.contracts.lifecycle import CleanupExecutionResult
from ragkb.domain.lifecycle import CleanupStatus, LifecycleState, SecurityTransitionStatus


def _service() -> tuple[LifecycleService, InMemoryLifecycleStore]:
    class _SuccessfulCleanup:
        revision = "test-cleanup"

        def execute(self, document_id: str) -> CleanupExecutionResult:
            return CleanupExecutionResult(bool(document_id), True)

    store = InMemoryLifecycleStore()
    service = LifecycleService(store, cleanup_executors={"local_file": _SuccessfulCleanup()})
    service.register_document("doc-1", "v1", trace_id="trace-register")
    return service, store


def test_publish_rollback_and_duplicate_event_are_idempotent() -> None:
    service, store = _service()

    published = service.publish("doc-1", "v2", event_id="publish-1", trace_id="trace-publish")
    duplicate = service.publish("doc-1", "v2", event_id="publish-1", trace_id="trace-duplicate")
    assert duplicate.active_version_id == published.active_version_id == "v2"
    assert duplicate.row_version == published.row_version
    rolled_back = service.rollback("doc-1", "v1", event_id="rollback-1", trace_id="trace-rollback")

    assert rolled_back.active_version_id == "v1"
    assert [event.action for event in store.audit_events].count("document.published") == 1


def test_acl_transition_is_fail_closed_until_projection_and_watermark_verified() -> None:
    service, _ = _service()
    service.publish("doc-1", "v1", event_id="initial-publish", trace_id="setup")
    transition = service.begin_acl_transition(
        "doc-1", 2, 10, event_id="acl-1", trace_id="trace-acl"
    )

    failed = service.complete_acl_transition(
        transition.transition_id, 9, projection_ok=True, trace_id="trace-watermark"
    )

    assert failed.status is SecurityTransitionStatus.FAILED
    assert service.accessible("doc-1") is False
    verified = service.complete_acl_transition(
        transition.transition_id, 10, projection_ok=True, trace_id="trace-verified"
    )
    assert verified.status is SecurityTransitionStatus.VERIFIED
    assert service.accessible("doc-1") is True


def test_projection_failure_and_revoke_concurrency_never_reopens_visibility() -> None:
    service, _ = _service()
    service.publish("doc-1", "v1", event_id="initial-publish", trace_id="setup")
    transition = service.begin_acl_transition(
        "doc-1", 2, 10, event_id="acl-1", trace_id="trace-acl"
    )
    service.revoke("doc-1", event_id="revoke-1", trace_id="trace-revoke")

    failed = service.complete_acl_transition(
        transition.transition_id, 10, projection_ok=False, trace_id="trace-failed"
    )

    assert failed.status is SecurityTransitionStatus.FAILED
    assert service.accessible("doc-1") is False
    assert service.store.documents["doc-1"].lifecycle_state is LifecycleState.REVOKED


def test_delete_tombstone_cleanup_and_restore_never_resurrects_content() -> None:
    service, store = _service()
    snapshot = deepcopy(store.documents["doc-1"])

    tombstone = service.delete("doc-1", event_id="delete-1", trace_id="trace-delete")
    service.run_cleanup("doc-1", "local_file", trace_id="trace-local")
    restored = service.restore_snapshot(snapshot, trace_id="trace-restore")

    assert service.accessible("doc-1") is False
    assert restored.lifecycle_state is LifecycleState.DELETED
    assert restored.active_version_id is None
    assert tombstone.cleanup["local_file"] is CleanupStatus.COMPLETED
    assert all(
        tombstone.cleanup[target] is CleanupStatus.PENDING_APPROVAL
        for target in ("mysql", "redis", "zilliz_projection")
    )


def test_audit_chain_is_append_only_and_trace_linked() -> None:
    service, store = _service()
    service.publish("doc-1", "v2", event_id="publish", trace_id="trace-publish")
    service.revoke("doc-1", event_id="revoke", trace_id="trace-revoke")

    events = tuple(store.audit_events)
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
    assert events[1].previous_hash == events[0].event_hash
    assert events[-1].previous_hash == events[-2].event_hash
    assert events[-1].trace_id == "trace-revoke"
    assert events[-1].governance_revision == LifecycleService.revision
