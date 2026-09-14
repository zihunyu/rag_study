"""Operator review of persisted missing sources through the normal lifecycle service."""

import time
from pathlib import Path
from typing import Any, Literal

from ragkb.adapters.directory_ledger import SQLiteDirectorySyncLedger
from ragkb.application.directory_sync import DirectorySync, digest
from ragkb.domain.uploads import OptimisticConcurrencyError, ResourceNotFoundError
from ragkb.runtime_components import RuntimeComponents


class DirectoryRemovalService:
    def __init__(self, runtime: RuntimeComponents) -> None:
        self.runtime = runtime
        self.ledger = SQLiteDirectorySyncLedger(runtime.storage.root / "sync/directory.sqlite3")

    def scope(self, space: str) -> str:
        if self.runtime.repository.get_space(space)["tenant_id"] != self.runtime.tenant_id:
            raise ResourceNotFoundError(space)
        return digest((self.runtime.tenant_id, space))

    def list(self, space: str) -> list[dict[str, Any]]:
        events = self.ledger.entries("removal:" + self.scope(space) + ":")
        return sorted(events.values(), key=lambda e: (e["updated_at"], e["id"]), reverse=True)

    def review(
        self,
        space: str,
        identity: str,
        actor: str,
        revision: int,
        action: Literal["withdraw", "keep", "reopen"],
        note: str,
    ) -> dict[str, Any]:
        scope = self.scope(space)
        key = "removal:" + scope + ":" + identity
        with self.ledger.lock(scope):
            event = self.ledger.get(key)
            if not event:
                raise ResourceNotFoundError(identity)
            target = {"withdraw": "withdrawn", "keep": "kept", "reopen": "pending"}[action]
            if (
                event["state"] == target
                and event.get("actor_id") == actor
                and event.get("note") == note
            ):
                return event
            if event["revision"] != revision:
                raise OptimisticConcurrencyError(identity)
            if action != "reopen" and event["state"] not in {"pending", "failed"}:
                raise ValueError("REMOVAL_REVIEW_NOT_PENDING")
            if action == "reopen" and event["state"] == "withdrawn":
                raise ValueError("WITHDRAWN_DOCUMENT_REQUIRES_NORMAL_PUBLICATION_REVIEW")
            if action == "withdraw":
                try:
                    self._withdraw(space, scope, event)
                except Exception as error:
                    self.ledger.put(
                        key,
                        {
                            **event,
                            "state": "failed",
                            "revision": revision + 1,
                            "updated_at": time.time(),
                            "last_error": type(error).__name__,
                            "actor_id": actor,
                            "note": note,
                            "history": [
                                *event.get("history", []),
                                {
                                    "action": action,
                                    "actor_id": actor,
                                    "note": note,
                                    "at": time.time(),
                                    "state": "failed",
                                    "error": type(error).__name__,
                                },
                            ],
                        },
                    )
                    raise
            updated = {
                **event,
                "state": target,
                "revision": revision + 1,
                "updated_at": time.time(),
                "actor_id": actor,
                "note": note,
                "last_error": "",
                "history": [
                    *event.get("history", []),
                    {"action": action, "actor_id": actor, "note": note, "at": time.time()},
                ],
            }
            self.ledger.put(key, updated)
            return updated

    def _withdraw(self, space: str, scope: str, event: dict[str, Any]) -> None:
        runtime = self.runtime
        snapshots = self.ledger.snapshots(scope)
        if not any(s["root"] == event["root"] for s in snapshots):
            raise ValueError("DIRECTORY_MANIFEST_MISSING_RESYNC_REQUIRED")
        scanner = DirectorySync(
            runtime.uploads,
            self.ledger,
            lambda kind: kind,
            max_files=runtime.settings.directory_sync_max_files,
        )
        for snapshot in snapshots:
            root = Path(snapshot["root"])
            if not root.is_dir():
                raise ValueError("DIRECTORY_SOURCE_UNAVAILABLE_RESYNC_REQUIRED")
            files, skipped = scanner.scan(root, verify_content=True)
            current = {f["path"]: f for f in files}
            saved = snapshot.get("files", {})
            if (
                set(current) - set(saved)
                or set(saved) - set(current) - set(skipped)
                or any(f["sha256"] != saved[p].get("sha256") for p, f in current.items())
            ):
                raise ValueError("DIRECTORY_CHANGED_RESYNC_REQUIRED")
            if any(v.get("document_id") == event["document_id"] for v in saved.values()):
                raise ValueError("DOCUMENT_STILL_HAS_SOURCE_REFERENCE")
        state = runtime.repository.directory_sync_states([event["document_version_id"]]).get(
            event["document_version_id"], {}
        )
        if (
            state.get("document_id") != event["document_id"]
            or state.get("space_id") != space
            or state.get("tenant_id") != runtime.tenant_id
            or state.get("latest_version_id") != event["document_version_id"]
        ):
            raise ValueError("DOCUMENT_CHANGED_REMOVAL_REVIEW_STALE")
        with runtime.lifecycle_store.lock:
            runtime.lifecycle_store.reload()
            record = runtime.lifecycle_store.documents.get(event["document_id"])
            if record and record.active_version_id not in {None, event["document_version_id"]}:
                raise ValueError("DOCUMENT_CHANGED_REMOVAL_REVIEW_STALE")
            if event["document_id"] not in runtime.lifecycle_store.documents:
                runtime.lifecycle_service.register_document(
                    event["document_id"], event["document_version_id"], trace_id=event["id"]
                )
            runtime.lifecycle_service.revoke(
                event["document_id"],
                event_id="directory-removal:" + event["id"],
                trace_id=event["id"],
            )
            runtime.reference_signer.revoke_document(event["document_id"])
