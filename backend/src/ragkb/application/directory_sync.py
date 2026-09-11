"""Incremental directory imports through the normal quarantine and ingestion queue."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ragkb.application.uploads import UploadService
from ragkb.contracts.directory_sync import DirectorySyncLedgerPort
from ragkb.domain.state_machines import UploadSessionState
from ragkb.engineering_security.file_validation import FORMAT_BY_EXTENSION


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()


class DirectorySync:
    """Local operator service. The ledger is a reuse hint; repository/queue stay authoritative."""

    def __init__(
        self,
        uploads: UploadService,
        ledger: DirectorySyncLedgerPort,
        contract: Callable[[str], str],
        *,
        max_files: int = 10000,
    ) -> None:
        self.uploads, self.repository, self.queue = uploads, uploads.repository, uploads.queue
        self.ledger = ledger
        self.path = ledger.path
        self.contract = contract
        self.max_files = max_files

    def _get(self, key: str) -> dict[str, Any]:
        return self.ledger.get(key)

    def _put(self, key: str, value: dict[str, Any]) -> None:
        self.ledger.put(key, value)

    def _assert_source(self, root: Path, source: Path) -> None:
        if source.is_symlink() or source.is_junction() or not source.resolve().is_relative_to(root):
            raise ValueError("DIRECTORY_SOURCE_ESCAPES_ROOT")

    def scan(self, root: Path) -> tuple[list[dict[str, Any]], list[str]]:
        files, skipped = [], []

        def scan_error(error: OSError) -> None:
            raise error

        for directory, dirs, names in os.walk(root, followlinks=False, onerror=scan_error):
            base = Path(directory)
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in {".git", ".venv", "node_modules", "__pycache__"}
                and not (base / d).is_symlink()
                and not (base / d).is_junction()
            )
            for name in sorted(names):
                source = base / name
                relative = source.relative_to(root).as_posix()
                if source.suffix.lower() not in FORMAT_BY_EXTENSION:
                    skipped.append(relative)
                    continue
                self._assert_source(root, source)
                size = source.stat().st_size
                if size > self.uploads.validator.max_size_bytes:
                    raise ValueError("DIRECTORY_FILE_SIZE_LIMIT:" + relative)
                before = source.stat()
                with source.open("rb") as handle:
                    checksum = hashlib.file_digest(handle, "sha256").hexdigest()
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("DIRECTORY_SOURCE_CHANGED_DURING_SCAN:" + relative)
                kind, mime = FORMAT_BY_EXTENSION[source.suffix.lower()]
                if kind in self.uploads.unsupported_formats or kind == "audio":
                    skipped.append(relative)
                    continue
                contract = self.contract(kind)
                files.append(
                    {
                        "path": relative,
                        "sha256": checksum,
                        "size": size,
                        "format": kind,
                        "mime": mime,
                        "contract": contract,
                        "content_key": digest((checksum, kind, contract)),
                    }
                )
                if len(files) > self.max_files:
                    raise ValueError("DIRECTORY_FILE_COUNT_LIMIT")
        return sorted(files, key=lambda x: x["path"]), skipped

    def _live(self, record: dict[str, Any], item: dict[str, Any], space_id: str) -> str:
        if not record:
            return ""
        try:
            version = self.repository.get_version(record["document_version_id"])
            document = self.repository.get_document(record["document_id"])
            versions = self.repository.get_versions(record["document_id"])
            if (
                version["content_sha256"] != item["sha256"]
                or not versions
                or versions[-1]["id"] != record["document_version_id"]
                or version["tenant_id"] != self.uploads.tenant_id
                or self.repository.get_document_space(record["document_id"]) != space_id
                or document["state"] != "ACTIVE"
            ):
                return ""
            job = self.queue.get(record["job_id"])
            if job is None:
                return ""
            return str(job.state.value)
        except KeyError:
            return ""

    def run(
        self, root: Path, space_id: str, *, apply: bool = False, retry_failed: bool = False
    ) -> dict[str, Any]:
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("DIRECTORY_ROOT_NOT_DIRECTORY")
        if self.repository.get_space(space_id)["tenant_id"] != self.uploads.tenant_id:
            raise ValueError("DIRECTORY_SPACE_TENANT_MISMATCH")
        # One operator may update a space at a time; API uploads still use row-version checks.
        scope = digest((self.uploads.tenant_id, space_id))
        with self.ledger.lock(scope):
            return self._run(resolved, space_id, scope, apply, retry_failed)

    def _run(
        self, root: Path, space_id: str, scope: str, apply: bool, retry_failed: bool
    ) -> dict[str, Any]:
        files, skipped = self.scan(root)
        snapshot_key = "root:" + scope + ":" + digest(str(root))
        previous = self._get(snapshot_key).get("files", {})
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in files:
            grouped.setdefault(item["content_key"], []).append(item)
        manifest = digest(files)
        output: list[dict[str, Any]] = []
        current: dict[str, Any] = {}
        # Include all registered roots so a changed alias never overwrites another source.
        references: dict[str, set[tuple[str, str]]] = {}
        for saved in self.ledger.snapshots(scope):
            for path, value in saved.get("files", {}).items():
                if value.get("document_id"):
                    references.setdefault(value["document_id"], set()).add((saved["root"], path))
        for content_key, aliases in grouped.items():
            item = aliases[0]
            key = "content:" + scope + ":" + content_key
            record = self._get(key).get("result", {})
            state = self._live(record, item, space_id)
            failed = state in {"FAILED_FINAL", "CANCELLED"}
            paths = [x["path"] for x in aliases]
            if state and not (failed and retry_failed):
                action = "failed" if failed else "reused"
            else:
                old: dict[str, Any] = next((previous[p] for p in paths if p in previous), {})
                candidate = old.get("document_id") or (
                    record.get("document_id") if failed else None
                )
                target = None
                if candidate:
                    external = references.get(candidate, set()) - {(str(root), p) for p in paths}
                    if not external:
                        try:
                            doc = self.repository.get_document(candidate)
                            if (
                                doc["state"] == "ACTIVE"
                                and doc["tenant_id"] == self.uploads.tenant_id
                                and self.repository.get_document_space(candidate) == space_id
                            ):
                                target = doc
                        except KeyError:
                            pass
                action = "updated" if target else "created"
                if apply:
                    result = self._submit(
                        root,
                        item,
                        key,
                        space_id,
                        target,
                        replaces_job=str(record.get("job_id", "")),
                    )
                    record, state = result, "QUEUED"
                    self._put(key, {"result": result})
            output.append(
                {
                    **item,
                    **record,
                    "action": action,
                    "job_state": state,
                    "aliases": paths,
                    "duplicate_count": len(paths) - 1,
                }
            )
            for alias in aliases:
                current[alias["path"]] = {**alias, **record}
        removed = sorted(set(previous) - set(current))
        if apply:
            self._put(snapshot_key, {"root": str(root), "manifest": manifest, "files": current})
        return {
            "mode": "apply" if apply else "preview",
            "root": str(root),
            "manifest": manifest,
            "files": len(files),
            "unique_contents": len(grouped),
            "duplicates": len(files) - len(grouped),
            "skipped": skipped,
            "removed_sources": removed,
            "results": output,
            "created": sum(x["action"] == "created" for x in output),
            "updated": sum(x["action"] == "updated" for x in output),
            "reused": sum(x["action"] == "reused" for x in output),
            "failed": sum(x["action"] == "failed" for x in output),
        }

    def _submit(
        self,
        root: Path,
        item: dict[str, Any],
        key: str,
        space_id: str,
        target: dict[str, Any] | None,
        *,
        replaces_job: str,
    ) -> dict[str, Any]:
        intent_key = "intent:" + key
        intent = self._get(intent_key)
        if replaces_job and intent.get("replaces_job") != replaces_job:
            intent = {}
        if not intent:
            intent = {
                "operation": uuid.uuid4().hex,
                "filename": Path(item["path"]).name,
                "replaces_job": replaces_job,
                "target_document_id": target["id"] if target else None,
                "target_document_row_version": target["row_version"] if target else None,
            }
            self._put(intent_key, intent)
        session = self.uploads.create_session(
            space_id=space_id,
            filename=intent["filename"],
            expected_size=item["size"],
            expected_sha256=item["sha256"],
            declared_mime=item["mime"],
            idempotency_key="directory:" + intent["operation"],
            target_document_id=intent["target_document_id"],
            target_document_row_version=intent["target_document_row_version"],
        )
        if session.state in {UploadSessionState.CREATED, UploadSessionState.FAILED}:
            source = root / item["path"]
            self._assert_source(root, source)

            async def upload() -> Any:
                async def stream() -> Any:
                    with source.open("rb") as handle:
                        while data := handle.read(256 * 1024):
                            yield data

                return await self.uploads.upload_content_stream(
                    session.id,
                    stream(),
                    expected_row_version=session.row_version,
                    content_length=item["size"],
                )

            session = asyncio.run(upload())
        return self.uploads.complete(
            session.id,
            expected_row_version=session.row_version,
            idempotency_key="directory-complete:" + intent["operation"],
        )
