"""Durable source-removal review; removal does not imply physical deletion."""

import time
from collections.abc import Sequence
from typing import Any

from ragkb.domain.ids import new_uuid7


def reconcile_removals(
    scope: str,
    root: str,
    space: str,
    tenant: str,
    previous: dict[str, Any],
    current: dict[str, Any],
    snapshots: Sequence[dict[str, Any]],
    saved: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Commit these events and the new manifest in the same ledger transaction."""
    references = {
        value["document_id"]
        for snapshot in snapshots
        if snapshot["root"] != root
        for value in snapshot.get("files", {}).values()
        if value.get("document_id")
    } | {v["document_id"] for v in current.values() if v.get("document_id")}
    updates = {}
    for key, event in saved.items():
        if event["document_id"] in references and event["state"] in {"pending", "failed"}:
            updates[key] = {
                **event,
                "state": "cancelled",
                "reason": "source_referenced",
                "revision": event["revision"] + 1,
                "updated_at": time.time(),
            }
    missing: dict[tuple[str, str], list[str]] = {}
    for path in previous.keys() - current.keys():
        old = previous[path]
        document, version = old.get("document_id"), old.get("document_version_id")
        if document and version and document not in references:
            missing.setdefault((document, version), []).append(path)
    for (document, version), paths in missing.items():
        existing = next(
            (
                e
                for e in saved.values()
                if e["document_id"] == document
                and e["document_version_id"] == version
                and e["state"] in {"pending", "failed", "kept"}
            ),
            None,
        )
        if existing:
            continue
        identity, now = new_uuid7(), time.time()
        event = {
            "id": identity,
            "tenant_id": tenant,
            "space_id": space,
            "document_id": document,
            "document_version_id": version,
            "root": root,
            "removed_paths": sorted(paths),
            "state": "pending",
            "revision": 1,
            "created_at": now,
            "updated_at": now,
            "actor_id": "",
            "note": "",
        }
        updates[f"removal:{scope}:{identity}"] = event
    return updates
