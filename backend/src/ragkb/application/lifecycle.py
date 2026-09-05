"""Fail-closed local lifecycle orchestration and deterministic fault injection for G3."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from copy import deepcopy
from dataclasses import asdict
from functools import wraps
from typing import Any, Concatenate, Protocol, cast

from ragkb.contracts.lifecycle import CleanupExecutorPort, PublicationReadinessPort
from ragkb.contracts.ports import DocumentProjectionPort
from ragkb.domain.ids import new_uuid7
from ragkb.domain.lifecycle import (
    AuditEvent,
    CleanupStatus,
    DeletionTombstone,
    LifecycleRecord,
    LifecycleState,
    SecurityTransition,
    SecurityTransitionStatus,
)
from ragkb.domain.retrieval import AuthorizedChunk, SearchContext, SecurityProjection
from ragkb.domain.uploads import OptimisticConcurrencyError

CLEANUP_STORES = ("local_file", "mysql", "redis", "zilliz_projection")
EXTERNAL_CLEANUP_STORES = frozenset({"mysql", "redis", "zilliz_projection"})


class _AtomicLifecycleService(Protocol):
    def _atomic(self) -> AbstractContextManager[None]: ...


def atomic_lifecycle_mutation[Service: _AtomicLifecycleService, **P, R](
    method: Callable[Concatenate[Service, P], R],
) -> Callable[Concatenate[Service, P], R]:
    @wraps(method)
    def wrapped(self: Service, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._atomic():
            return method(self, *args, **kwargs)

    return cast(Callable[Concatenate[Service, P], R], wrapped)


class InMemoryLifecycleStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.documents: dict[str, LifecycleRecord] = {}
        self.transitions: dict[str, SecurityTransition] = {}
        self.tombstones: dict[str, DeletionTombstone] = {}
        self.audit_events: list[AuditEvent] = []
        self.processed_events: set[str] = set()
        self.idempotency: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}

    def reload(self) -> None:
        """Refresh authoritative state; in-memory tests already hold the authoritative copy."""

    def snapshot_state(self) -> dict[str, Any]:
        return deepcopy(
            {
                "documents": self.documents,
                "transitions": self.transitions,
                "tombstones": self.tombstones,
                "audit_events": self.audit_events,
                "processed_events": self.processed_events,
                "idempotency": self.idempotency,
            }
        )

    def restore_state(self, snapshot: Mapping[str, Any]) -> None:
        self.documents = deepcopy(snapshot["documents"])
        self.transitions = deepcopy(snapshot["transitions"])
        self.tombstones = deepcopy(snapshot["tombstones"])
        self.audit_events = deepcopy(snapshot["audit_events"])
        self.processed_events = deepcopy(snapshot["processed_events"])
        self.idempotency = deepcopy(snapshot["idempotency"])

    def get_idempotency(
        self, tenant_id: str, operation: str, key: str
    ) -> tuple[str, dict[str, Any]] | None:
        return self.idempotency.get((tenant_id, operation, key))

    def persist_state(
        self,
        *,
        tenant_id: str = "local",
        operation: str | None = None,
        key: str | None = None,
        request_hash: str | None = None,
        response: dict[str, Any] | None = None,
        expected_document_row_version: int | None = None,
        generation_id: str | None = None,
    ) -> None:
        del expected_document_row_version, generation_id
        if operation and key and request_hash and response is not None:
            self.idempotency[(tenant_id, operation, key)] = (request_hash, dict(response))

    def is_tombstoned(self, document_id: str) -> bool:
        return document_id in self.tombstones or (
            document_id in self.documents and self.documents[document_id].tombstoned
        )

    def is_accessible(self, document_id: str) -> bool:
        record = self.documents.get(document_id)
        return record is not None and (
            record.lifecycle_state is LifecycleState.ACTIVE
            and record.visible
            and not record.tombstoned
            and record.active_version_id is not None
        )

    def authorizes_chunk(self, chunk: AuthorizedChunk, context: SearchContext) -> bool:
        record = self.documents.get(chunk.document_id)
        return bool(
            record
            and self.is_accessible(chunk.document_id)
            and record.active_version_id == chunk.document_version_id
            and chunk.permission_revision == record.acl_revision
            and chunk.permission_revision <= context.active_permission_revision
        )


class LifecycleIdempotencyConflict(ValueError):
    pass


class LifecycleStateConflict(ValueError):
    pass


class CleanupApprovalRequired(PermissionError):
    pass


class LifecycleService:
    revision = "lifecycle-orchestration:g3-v1"

    def __init__(
        self,
        store: InMemoryLifecycleStore,
        tenant_id: str = "local",
        *,
        cleanup_executors: Mapping[str, CleanupExecutorPort] | None = None,
        publication_readiness: PublicationReadinessPort | None = None,
        retrieval_projection: DocumentProjectionPort | None = None,
        allow_external_cleanup: bool = False,
    ) -> None:
        self.store = store
        self.tenant_id = tenant_id
        self.cleanup_executors = dict(cleanup_executors or {})
        self.publication_readiness = publication_readiness
        self.retrieval_projection = retrieval_projection
        self.allow_external_cleanup = allow_external_cleanup

    def _project_document(
        self,
        record: LifecycleRecord,
        *,
        lifecycle_projection: str,
        active_version_id: str | None = None,
    ) -> None:
        if self.retrieval_projection is None:
            return
        self.retrieval_projection.set_document_projection(
            record.document_id,
            active_version_id=(
                record.active_version_id if active_version_id is None else active_version_id
            ),
            lifecycle_projection=lifecycle_projection,
            permission_revision=record.acl_revision,
        )

    def set_reviewed_security_projection(
        self,
        document_id: str,
        version_id: str,
        projection: SecurityProjection,
        *,
        trace_id: str,
    ) -> None:
        if self.retrieval_projection is None:
            raise LifecycleStateConflict("RETRIEVAL_PROJECTION_UNAVAILABLE")
        if projection.lifecycle_projection != "STAGED":
            raise ValueError("reviewed security projection must remain staged")
        record = self.store.documents.get(document_id)
        if record is None or record.tombstoned:
            raise LifecycleStateConflict("DOCUMENT_NOT_AVAILABLE")
        self.retrieval_projection.set_version_security_projection(
            document_id, version_id, projection
        )
        self._audit("document.security_projection_reviewed", document_id, trace_id)
        self.store.persist_state(tenant_id=self.tenant_id)

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        with self.store.lock:
            snapshot = self.store.snapshot_state()
            try:
                yield
            except Exception:
                # A persisted transition is a safety barrier, not a rollback candidate.
                # Restore the last committed snapshot rather than resurrecting SERVING.
                self.store.restore_state(getattr(self.store, "_committed_state", snapshot))
                raise

    @staticmethod
    def _hash(payload: Mapping[str, object]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded, usedforsecurity=False).hexdigest()

    def _replay(
        self, operation: str, key: str, payload: Mapping[str, object]
    ) -> dict[str, Any] | None:
        existing = self.store.get_idempotency(self.tenant_id, operation, key)
        if existing is None:
            return None
        request_hash, response = existing
        if request_hash != self._hash(payload):
            raise LifecycleIdempotencyConflict(
                "idempotency key reused with a different lifecycle request"
            )
        return response

    def _persist(
        self,
        operation: str,
        key: str,
        payload: Mapping[str, object],
        response: dict[str, Any],
        *,
        expected_document_row_version: int | None = None,
        generation_id: str | None = None,
    ) -> None:
        self.store.persist_state(
            tenant_id=self.tenant_id,
            operation=operation,
            key=key,
            request_hash=self._hash(payload),
            response=response,
            expected_document_row_version=expected_document_row_version,
            generation_id=generation_id,
        )

    @staticmethod
    def _record_dict(record: LifecycleRecord) -> dict[str, Any]:
        return {
            **asdict(record),
            "lifecycle_state": record.lifecycle_state.value,
        }

    @staticmethod
    def _record_from(data: dict[str, Any]) -> LifecycleRecord:
        return LifecycleRecord(
            **{
                **data,
                "version_history": list(data["version_history"]),
                "lifecycle_state": LifecycleState(data["lifecycle_state"]),
            }
        )

    def _audit(self, action: str, resource_id: str, trace_id: str) -> AuditEvent:
        previous = self.store.audit_events[-1].event_hash if self.store.audit_events else "0" * 64
        sequence = len(self.store.audit_events) + 1
        payload = json.dumps(
            {
                "sequence": sequence,
                "action": action,
                "resource_id": resource_id,
                "trace_id": trace_id,
                "governance_revision": self.revision,
                "previous_hash": previous,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        event_hash = hashlib.sha256(payload.encode(), usedforsecurity=False).hexdigest()
        event = AuditEvent(
            sequence,
            action,
            resource_id,
            trace_id,
            self.revision,
            previous,
            event_hash,
        )
        self.store.audit_events.append(event)
        return event

    def _new_event(self, event_id: str) -> bool:
        if event_id in self.store.processed_events:
            return False
        self.store.processed_events.add(event_id)
        return True

    @atomic_lifecycle_mutation
    def register_document(self, document_id: str, version_id: str, *, trace_id: str) -> None:
        if document_id in self.store.documents:
            raise ValueError("document already registered")
        self.store.documents[document_id] = LifecycleRecord(document_id, version_id)
        self._audit("document.registered", document_id, trace_id)
        self.store.persist_state(tenant_id=self.tenant_id)

    @atomic_lifecycle_mutation
    def publish(
        self, document_id: str, version_id: str, *, event_id: str, trace_id: str
    ) -> LifecycleRecord:
        operation = f"publish:{document_id}"
        payload = {"document_id": document_id, "version_id": version_id}
        record = self.store.documents[document_id]
        if record.tombstoned or record.lifecycle_state is LifecycleState.DELETED:
            raise LifecycleStateConflict("DOCUMENT_DELETED")
        replay = self._replay(operation, event_id, payload)
        if replay is not None:
            return self._record_from(replay)
        if (
            record.lifecycle_state is LifecycleState.ACTIVE
            and record.visible
            and record.active_version_id == version_id
        ):
            return record
        readiness = (
            self.publication_readiness.check(document_id, version_id)
            if self.publication_readiness is not None
            else None
        )
        if readiness is not None and not readiness.ready:
            raise LifecycleStateConflict(readiness.error_code or "PUBLICATION_NOT_READY")
        if record.active_version_id and record.active_version_id != version_id:
            record.version_history.append(record.active_version_id)
        record.lifecycle_state = LifecycleState.SWITCHING
        record.visible = False
        self._audit("publication.switching", document_id, trace_id)
        record.active_version_id = version_id
        record.row_version += 1
        durable_intent = bool(getattr(self.store, "durable_publication_intents", False))
        if durable_intent:
            self._persist(
                f"{operation}:intent",
                event_id,
                payload,
                self._record_dict(record),
                expected_document_row_version=(
                    readiness.document_row_version if readiness is not None else None
                ),
                generation_id=readiness.generation_id if readiness is not None else None,
            )
        try:
            self._project_document(record, lifecycle_projection="SERVING")
        except Exception as error:
            mark_failed = getattr(self.store, "mark_publication_projection_failed", None)
            if callable(mark_failed):
                mark_failed(operation, event_id, type(error).__name__)
            raise
        record.lifecycle_state = LifecycleState.ACTIVE
        record.visible = True
        self._audit("publication.projection_swapped", document_id, trace_id)
        self._audit("document.published", document_id, trace_id)
        self._persist(
            operation,
            event_id,
            payload,
            self._record_dict(record),
            expected_document_row_version=(
                readiness.document_row_version if readiness is not None else None
            ),
            generation_id=readiness.generation_id if readiness is not None else None,
        )
        return record

    @atomic_lifecycle_mutation
    def rollback(
        self, document_id: str, version_id: str, *, event_id: str, trace_id: str
    ) -> LifecycleRecord:
        operation = f"rollback:{document_id}"
        payload = {"document_id": document_id, "version_id": version_id}
        record = self.store.documents[document_id]
        if record.tombstoned or record.lifecycle_state is LifecycleState.DELETED:
            raise LifecycleStateConflict("DOCUMENT_DELETED")
        replay = self._replay(operation, event_id, payload)
        if replay is not None:
            return self._record_from(replay)
        if (
            record.lifecycle_state is LifecycleState.ACTIVE
            and record.visible
            and record.active_version_id == version_id
        ):
            return record
        if version_id not in record.version_history:
            raise ValueError("rollback target is unavailable")
        readiness = (
            self.publication_readiness.check(document_id, version_id, rollback=True)
            if self.publication_readiness is not None
            else None
        )
        if readiness is not None and not readiness.ready:
            raise LifecycleStateConflict(readiness.error_code or "ROLLBACK_NOT_READY")
        if record.active_version_id:
            record.version_history.append(record.active_version_id)
        record.lifecycle_state = LifecycleState.SWITCHING
        record.visible = False
        self._audit("rollback.switching", document_id, trace_id)
        record.active_version_id = version_id
        record.row_version += 1
        durable_intent = bool(getattr(self.store, "durable_publication_intents", False))
        if durable_intent:
            self._persist(
                f"{operation}:intent",
                event_id,
                payload,
                self._record_dict(record),
                expected_document_row_version=(
                    readiness.document_row_version if readiness is not None else None
                ),
                generation_id=readiness.generation_id if readiness is not None else None,
            )
        try:
            self._project_document(record, lifecycle_projection="SERVING")
        except Exception as error:
            mark_failed = getattr(self.store, "mark_publication_projection_failed", None)
            if callable(mark_failed):
                mark_failed(operation, event_id, type(error).__name__)
            raise
        record.lifecycle_state = LifecycleState.ACTIVE
        record.visible = True
        self._audit("rollback.projection_swapped", document_id, trace_id)
        self._audit("document.rolled_back", document_id, trace_id)
        self._persist(
            operation,
            event_id,
            payload,
            self._record_dict(record),
            expected_document_row_version=(
                readiness.document_row_version if readiness is not None else None
            ),
            generation_id=readiness.generation_id if readiness is not None else None,
        )
        return record

    @atomic_lifecycle_mutation
    def replace_permissions(
        self,
        document_id: str,
        policy: dict[str, Any],
        *,
        expected_row_version: int,
        event_id: str,
        trace_id: str,
    ) -> LifecycleRecord:
        self.store.reload()
        operation = f"permissions:{document_id}"
        payload = {
            "document_id": document_id,
            "policy": policy,
            "expected_row_version": expected_row_version,
        }
        replay = self._replay(operation, event_id, payload)
        if replay is not None:
            return self._record_from(replay)
        record = self.store.documents[document_id]
        if record.tombstoned or record.active_version_id is None:
            raise LifecycleStateConflict("DOCUMENT_NOT_AVAILABLE")
        pending_operation = f"security-policy-pending:{document_id}"
        pending = self._replay(pending_operation, event_id, payload)
        if pending is None:
            if record.row_version != expected_row_version:
                raise OptimisticConcurrencyError(document_id)
            if not self.store.is_accessible(document_id):
                raise LifecycleStateConflict("DOCUMENT_NOT_PUBLISHED")
            projection = SecurityProjection(
                visibility=policy["visibility"],
                classification_level=policy["classification_level"],
                acl_scope_tokens=tuple(policy["acl_scope_tokens"]),
                lifecycle_projection="STAGED",
                permission_revision=record.acl_revision + 1,
                valid_from_epoch=int(time.time()),
                valid_to_epoch=policy.get("valid_to_epoch", 0),
            )
            record.lifecycle_state = LifecycleState.SECURITY_TRANSITION
            record.visible = False
            record.row_version += 1
            pending = {
                "projection": asdict(projection),
                "version_id": record.active_version_id,
                "versions": sorted(set([record.active_version_id, *record.version_history])),
            }
            self._audit("acl.policy_pending", document_id, trace_id)
            self._persist(pending_operation, event_id, payload, pending)
        if self.retrieval_projection is None:
            raise LifecycleStateConflict("RETRIEVAL_PROJECTION_UNAVAILABLE")
        if pending["version_id"] != record.active_version_id:
            raise LifecycleStateConflict("SECURITY_TARGET_VERSION_CHANGED")
        projection = SecurityProjection(
            **{
                **pending["projection"],
                "acl_scope_tokens": tuple(pending["projection"]["acl_scope_tokens"]),
            }
        )
        try:
            # Document permissions must survive rollback to any retained version.
            for version_id in pending.get("versions", [record.active_version_id]):
                self.retrieval_projection.set_version_security_projection(
                    document_id, version_id, projection
                )
            record.acl_revision = projection.permission_revision
            self._project_document(record, lifecycle_projection="SERVING")
            record.visible = True
            record.lifecycle_state = LifecycleState.ACTIVE
            record.row_version += 1
            self._audit("acl.policy_applied", document_id, trace_id)
            self._persist(operation, event_id, payload, self._record_dict(record))
        except Exception:
            self.store.reload()
            raise
        return record

    @atomic_lifecycle_mutation
    def begin_acl_transition(
        self,
        document_id: str,
        target_acl_revision: int,
        required_watermark: int,
        *,
        event_id: str,
        trace_id: str,
        idempotency_context: Mapping[str, object] | None = None,
        persist_idempotency: bool = True,
    ) -> SecurityTransition:
        operation = f"acl:{document_id}"
        payload = {
            "document_id": document_id,
            "target_acl_revision": target_acl_revision,
            "required_watermark": required_watermark,
            **dict(idempotency_context or {}),
        }
        record = self.store.documents[document_id]
        if record.tombstoned or record.lifecycle_state is LifecycleState.DELETED:
            raise LifecycleStateConflict("DOCUMENT_DELETED")
        if record.lifecycle_state is not LifecycleState.ACTIVE:
            raise LifecycleStateConflict("DOCUMENT_NOT_PUBLISHED")
        if persist_idempotency:
            replay = self._replay(operation, event_id, payload)
            if replay is not None:
                return self.store.transitions[str(replay["transition_id"])]
        if target_acl_revision <= record.acl_revision:
            raise ValueError("ACL revision must increase on an active document")
        record.lifecycle_state = LifecycleState.SECURITY_TRANSITION
        record.visible = False
        record.row_version += 1
        self._project_document(record, lifecycle_projection="SECURITY_TRANSITION")
        transition = SecurityTransition(
            new_uuid7(), document_id, target_acl_revision, required_watermark
        )
        self.store.transitions[transition.transition_id] = transition
        self._audit("acl.transition_started", document_id, trace_id)
        if persist_idempotency:
            self._persist(
                operation,
                event_id,
                payload,
                {"transition_id": transition.transition_id},
            )
        return transition

    @atomic_lifecycle_mutation
    def update_acl(
        self,
        document_id: str,
        target_acl_revision: int,
        required_watermark: int,
        observed_watermark: int,
        *,
        projection_ok: bool,
        event_id: str,
        trace_id: str,
    ) -> LifecycleRecord:
        operation = f"acl-update:{document_id}"
        payload: dict[str, object] = {
            "document_id": document_id,
            "target_acl_revision": target_acl_revision,
            "required_watermark": required_watermark,
            "observed_watermark": observed_watermark,
            "projection_ok": projection_ok,
        }
        record = self.store.documents[document_id]
        if record.tombstoned or record.lifecycle_state is LifecycleState.DELETED:
            raise LifecycleStateConflict("DOCUMENT_DELETED")
        replay = self._replay(operation, event_id, payload)
        if replay is not None:
            return self._record_from(replay)
        transition = self.begin_acl_transition(
            document_id,
            target_acl_revision,
            required_watermark,
            event_id=event_id,
            trace_id=trace_id,
            persist_idempotency=False,
        )
        self.complete_acl_transition(
            transition.transition_id,
            observed_watermark,
            projection_ok=projection_ok,
            trace_id=trace_id,
            persist_state=False,
        )
        record = self.store.documents[document_id]
        self._persist(operation, event_id, payload, self._record_dict(record))
        return record

    @atomic_lifecycle_mutation
    def complete_acl_transition(
        self,
        transition_id: str,
        observed_watermark: int,
        *,
        projection_ok: bool,
        trace_id: str,
        persist_state: bool = True,
    ) -> SecurityTransition:
        transition = self.store.transitions[transition_id]
        record = self.store.documents[transition.document_id]
        if record.tombstoned or record.lifecycle_state is LifecycleState.DELETED:
            raise LifecycleStateConflict("DOCUMENT_DELETED")
        if transition.status is SecurityTransitionStatus.VERIFIED:
            return transition
        transition.observed_watermark = observed_watermark
        if record.lifecycle_state is not LifecycleState.SECURITY_TRANSITION:
            transition.status = SecurityTransitionStatus.FAILED
            transition.error_code = "SECURITY_STATE_CHANGED"
            record.visible = False
            self._audit("acl.transition_failed", record.document_id, trace_id)
            if persist_state:
                self.store.persist_state(tenant_id=self.tenant_id)
            return transition
        if not projection_ok or observed_watermark < transition.required_watermark:
            transition.status = SecurityTransitionStatus.FAILED
            transition.error_code = "PERMISSION_PROJECTION_NOT_READY"
            record.lifecycle_state = LifecycleState.SECURITY_TRANSITION
            record.visible = False
            self._audit("acl.transition_failed", record.document_id, trace_id)
            if persist_state:
                self.store.persist_state(tenant_id=self.tenant_id)
            return transition
        transition.status = SecurityTransitionStatus.VERIFIED
        record.acl_revision = transition.target_acl_revision
        record.lifecycle_state = LifecycleState.ACTIVE
        record.visible = True
        record.row_version += 1
        self._project_document(record, lifecycle_projection="SERVING")
        self._audit("acl.transition_verified", record.document_id, trace_id)
        if persist_state:
            self.store.persist_state(tenant_id=self.tenant_id)
        return transition

    @atomic_lifecycle_mutation
    def revoke(self, document_id: str, *, event_id: str, trace_id: str) -> LifecycleRecord:
        operation = f"revoke:{document_id}"
        payload = {"document_id": document_id}
        record = self.store.documents[document_id]
        if record.tombstoned or record.lifecycle_state is LifecycleState.DELETED:
            raise LifecycleStateConflict("DOCUMENT_DELETED")
        replay = self._replay(operation, event_id, payload)
        if replay is not None:
            return self._record_from(replay)
        record.lifecycle_state = LifecycleState.REVOKED
        record.visible = False
        record.row_version += 1
        self._project_document(record, lifecycle_projection="REVOKED")
        self._audit("document.revoked", document_id, trace_id)
        self._persist(operation, event_id, payload, self._record_dict(record))
        return record

    @atomic_lifecycle_mutation
    def delete(self, document_id: str, *, event_id: str, trace_id: str) -> DeletionTombstone:
        existing_tombstone = self.store.tombstones.get(document_id)
        if existing_tombstone is not None:
            return existing_tombstone
        operation = f"delete:{document_id}"
        payload = {"document_id": document_id}
        replay = self._replay(operation, event_id, payload)
        if replay is not None:
            return DeletionTombstone(
                document_id,
                {key: CleanupStatus(value) for key, value in replay["cleanup"].items()},
            )
        record = self.store.documents[document_id]
        record.lifecycle_state = LifecycleState.DELETED
        record.visible = False
        record.tombstoned = True
        record.active_version_id = None
        record.row_version += 1
        if self.retrieval_projection is not None:
            self.retrieval_projection.delete_document_projection(document_id)
        tombstone = DeletionTombstone(
            document_id,
            {
                store: (
                    CleanupStatus.PENDING_APPROVAL
                    if store in EXTERNAL_CLEANUP_STORES
                    else CleanupStatus.PENDING
                )
                for store in CLEANUP_STORES
            },
        )
        self.store.tombstones[document_id] = tombstone
        self._audit("document.deleted", document_id, trace_id)
        self._persist(
            operation,
            event_id,
            payload,
            {"cleanup": {key: value.value for key, value in tombstone.cleanup.items()}},
        )
        return tombstone

    @atomic_lifecycle_mutation
    def run_cleanup(
        self,
        document_id: str,
        store: str,
        *,
        trace_id: str,
        event_id: str | None = None,
    ) -> CleanupStatus:
        if store not in CLEANUP_STORES:
            raise ValueError("unknown cleanup store")
        if store in EXTERNAL_CLEANUP_STORES and not self.allow_external_cleanup:
            raise CleanupApprovalRequired("EXTERNAL_CLEANUP_REQUIRES_APPROVAL")
        operation = f"cleanup:{document_id}:{store}"
        key = event_id or operation
        payload = {"document_id": document_id, "store": store}
        replay = self._replay(operation, key, payload)
        if replay is not None:
            return CleanupStatus(str(replay["status"]))
        executor = self.cleanup_executors.get(store)
        if executor is None:
            raise RuntimeError("CLEANUP_EXECUTOR_UNAVAILABLE")
        result = executor.execute(document_id)
        if not result.executed or not result.postcondition_met:
            self.store.tombstones[document_id].cleanup[store] = CleanupStatus.FAILED
            self._audit(f"cleanup.{store}.failed", document_id, trace_id)
            self.store.persist_state(tenant_id=self.tenant_id)
            return CleanupStatus.FAILED
        self.store.tombstones[document_id].cleanup[store] = CleanupStatus.COMPLETED
        self._audit(f"cleanup.{store}.completed", document_id, trace_id)
        self._persist(
            operation,
            key,
            payload,
            {"status": CleanupStatus.COMPLETED.value},
        )
        return CleanupStatus.COMPLETED

    @atomic_lifecycle_mutation
    def restore_snapshot(self, snapshot: LifecycleRecord, *, trace_id: str) -> LifecycleRecord:
        current = self.store.documents.get(snapshot.document_id)
        if snapshot.document_id in self.store.tombstones or (current and current.tombstoned):
            if current is None:
                current = LifecycleRecord(snapshot.document_id, None)
                self.store.documents[snapshot.document_id] = current
            current.lifecycle_state = LifecycleState.DELETED
            current.visible = False
            current.tombstoned = True
            current.active_version_id = None
            self._audit("restore.tombstone_replayed", snapshot.document_id, trace_id)
            self.store.persist_state(tenant_id=self.tenant_id)
            return current
        self.store.documents[snapshot.document_id] = snapshot
        self._audit("restore.document", snapshot.document_id, trace_id)
        self.store.persist_state(tenant_id=self.tenant_id)
        return snapshot

    def accessible(self, document_id: str) -> bool:
        record = self.store.documents[document_id]
        return (
            record.lifecycle_state is LifecycleState.ACTIVE
            and record.visible
            and not record.tombstoned
            and record.active_version_id is not None
        )
