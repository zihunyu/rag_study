"""Manager-only original-file acceptance with immutable local run snapshots."""

# ruff: noqa: S608 -- The lock suffix is a fixed dialect constant, never user data.

from __future__ import annotations

import json
import time
from pathlib import PurePosixPath
from typing import Any

from ragkb.api.support import ensure_document_previewable
from ragkb.domain.acceptance import fingerprint
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.parsing_acceptance import REVISION, ParsingStandard, evaluate, locations
from ragkb.domain.uploads import OptimisticConcurrencyError, ResourceNotFoundError
from ragkb.infrastructure.acceptance_repository import decoded, encode


class ParsingAcceptance:
    def __init__(self, service: Any) -> None:
        self.service, self.runtime, self.db = service, service.runtime, service.repository.db

    def version(self, subject: RequestPrincipal, space: str, identity: str) -> dict[str, Any]:
        self.service.authorize(subject, space)
        version = self.runtime.repository.get_version(identity)
        if self.runtime.repository.get_document_space(version["document_id"]) != space:
            raise ResourceNotFoundError(identity)
        ensure_document_previewable(self.runtime, version["document_id"], subject)
        return dict(version)

    def records(self, subject: RequestPrincipal, space: str, kind: str) -> list[dict[str, Any]]:
        self.service.authorize(subject, space)
        with self.db.connection() as c:
            rows = self.db.rows(
                c,
                "SELECT * FROM acceptance_parsing_records WHERE tenant_id=? AND space_id=? "
                "AND kind=? ORDER BY updated_at DESC LIMIT 500",
                (subject.tenant_id, space, kind),
            )
        visible = []
        for row in rows:
            value = decoded(row)
            try:
                self.version(subject, space, value["payload"]["reference_version_id"])
            except ResourceNotFoundError:
                continue
            visible.append(value)
        return visible

    def record(self, subject: RequestPrincipal, space: str, identity: str) -> dict[str, Any]:
        self.service.authorize(subject, space)
        with self.db.connection() as c:
            row = self.db.one(
                c,
                "SELECT * FROM acceptance_parsing_records "
                "WHERE tenant_id=? AND space_id=? AND id=?",
                (subject.tenant_id, space, identity),
            )
        if not row:
            raise ResourceNotFoundError(identity)
        value = decoded(row)
        self.version(subject, space, value["payload"]["reference_version_id"])
        if value["kind"] == "run":
            self.version(subject, space, value["payload"]["version_id"])
        return value

    def save(
        self, subject: RequestPrincipal, space: str, spec: ParsingStandard, revision: int
    ) -> dict[str, Any]:
        version = self.version(subject, space, spec.reference_version_id)
        if version["document_id"] != spec.document_id:
            raise ValueError("ORIGINAL_DOCUMENT_MISMATCH")
        identity = fingerprint([subject.tenant_id, space, "parsing", spec.key])[:36]
        payload = spec.model_dump(mode="json") | {"original_sha256": version["content_sha256"]}
        now = time.time()
        with self.db.transaction() as c:
            row = self.db.one(
                c,
                "SELECT * FROM acceptance_parsing_records WHERE id=?"
                + self.service.repository.lock(),
                (identity,),
            )
            if (row["revision"] if row else 0) != revision:
                raise OptimisticConcurrencyError(identity)
            if row:
                self.db.execute(
                    c,
                    "UPDATE acceptance_parsing_records SET payload_json=?,revision=?,actor_id=?,"
                    "updated_at=? WHERE id=?",
                    (encode(payload), revision + 1, subject.user_id, now, identity),
                )
            else:
                self._insert(c, identity, subject, space, "standard", revision + 1, payload, now)
            self._insert(
                c,
                f"{identity}:{revision + 1}",
                subject,
                space,
                "standard_revision",
                revision + 1,
                payload,
                now,
            )
        return self.record(subject, space, identity)

    def _insert(
        self,
        c: Any,
        identity: str,
        subject: RequestPrincipal,
        space: str,
        kind: str,
        revision: int,
        payload: dict[str, Any],
        now: float,
    ) -> None:
        self.db.execute(
            c,
            "INSERT INTO acceptance_parsing_records "
            "(id,tenant_id,space_id,kind,revision,actor_id,payload_json,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                identity,
                subject.tenant_id,
                space,
                kind,
                revision,
                subject.user_id,
                encode(payload),
                now,
            ),
        )

    def snapshot(
        self, subject: RequestPrincipal, space: str, identity: str, pages: set[int]
    ) -> dict[str, Any]:
        version = self.version(subject, space, identity)
        try:
            self.runtime.repository.get_quality_report(identity)
            chunks_ready = version["processing_state"] in {"VALIDATED", "INDEXED"}
        except ResourceNotFoundError:
            chunks_ready = False
        # The canonical artifact is saved before chunking. Never substitute chunks
        # for absent parser evidence, which would conceal a transformation loss.
        prefix = str(PurePosixPath(version["original_key"]).parent.parent) + "/artifacts/"
        parsed = None
        artifacts = self.runtime.repository.list_local_content_lineage(version["document_id"])
        candidates = [
            key
            for part, key in artifacts
            if part == "artifacts" and key.startswith(prefix) and "canonical-document" in key
        ]
        import re

        for key in sorted(
            candidates,
            key=lambda k: int(m[1]) if (m := re.search(r"-f(\d+)\.json$", k)) else 0,
            reverse=True,
        ):
            try:
                value = json.loads(self.runtime.storage.read_bytes("artifacts", key))
            except FileNotFoundError:
                continue
            if (
                value.get("document_version_id") == identity
                and value.get("content_checksum") == version["content_sha256"]
            ):
                parsed = [
                    {
                        "id": n["node_id"],
                        "text": n["display_text"],
                        "locator": n["locator"],
                        "type": n["type"],
                        "origin": "visual"
                        if n.get("metadata", {}).get("visual_asset_ids")
                        else "parser",
                    }
                    for n in value["nodes"]
                ]
                break
        chunks: list[dict[str, Any]] = []
        for offset in range(0, 100000, 500):
            page = self.runtime.repository.list_chunks_page(
                identity, limit=500, offset=offset, preview=True
            )
            chunks.extend(
                {
                    "id": r["chunk_id"],
                    "text": r["text"],
                    "locator": r["locator"],
                    "kind": r.get("kind", ""),
                    "origin": ("mixed" if r.get("kind") == "parent" else "visual")
                    if r["locator"].get("visual_asset_ids")
                    else "parser",
                }
                for r in page.items
            )
            if not page.next_key:
                break
        else:
            raise ValueError("PARSING_CHUNK_SCOPE_TOO_LARGE")
        return {
            "version_id": identity,
            "original_sha256": version["content_sha256"],
            "parser_revision": version.get("parser_revision"),
            "pages": sorted(pages),
            "parsed": [r for r in parsed if locations(r) & pages] if parsed is not None else None,
            "chunks": [r for r in chunks if locations(r) & pages] if chunks_ready else None,
            "chunks_note": "" if chunks_ready else "分块或索引仍未完成，暂不能判定内容丢失。",
        }

    def run(
        self,
        subject: RequestPrincipal,
        space: str,
        standard_id: str,
        version_id: str,
        request_key: str,
    ) -> dict[str, Any]:
        standard = self.record(subject, space, standard_id)
        spec = standard["payload"]
        if standard["kind"] != "standard" or not spec["original_checked"]:
            raise ValueError("CONFIRM_ORIGINAL_STANDARD_FIRST")
        version = self.version(subject, space, version_id)
        if (
            version["document_id"] != spec["document_id"]
            or version["content_sha256"] != spec["original_sha256"]
        ):
            raise ValueError("ORIGINAL_CHANGED_RECONFIRM_STANDARD")
        identity = fingerprint([subject.tenant_id, space, "parse-run", request_key])[:36]
        try:
            existing = self.record(subject, space, identity)
        except ResourceNotFoundError:
            existing = None
        if existing:
            if (
                existing["payload"]["standard_id"] != standard_id
                or existing["payload"]["version_id"] != version_id
                or existing["payload"]["standard_revision"] != standard["revision"]
            ):
                raise ValueError("PARSING_REQUEST_CHANGED")
            return existing
        started = time.monotonic()
        snapshot = self.snapshot(
            subject, space, version_id, {p for check in spec["checks"] for p in check["pages"]}
        )
        rows = evaluate(spec["checks"], snapshot)
        payload = {
            "reference_version_id": spec["reference_version_id"],
            "standard_id": standard_id,
            "standard_revision": standard["revision"],
            "standard": spec,
            "version_id": version_id,
            "snapshot": snapshot,
            "rows": rows,
            "evaluation_revision": REVISION,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "timing_scope": "local_validation_only",
            "verdict": "passed"
            if all(s["status"] == "found" for r in rows for s in r["stages"].values())
            else "failed"
            if any(s["status"] == "not_found" for r in rows for s in r["stages"].values())
            else "incomplete",
        }
        self.version(subject, space, version_id)
        with self.db.transaction() as c:
            self._insert(c, identity, subject, space, "run", 1, payload, time.time())
        return self.record(subject, space, identity)

    def compare(
        self, subject: RequestPrincipal, space: str, baseline: str, candidate: str
    ) -> dict[str, Any]:
        old, new = (self.record(subject, space, i) for i in (baseline, candidate))
        a, b = old["payload"], new["payload"]
        comparable = (
            old["kind"] == new["kind"] == "run"
            and all(
                a[k] == b[k] for k in ("standard_id", "standard_revision", "evaluation_revision")
            )
            and a["snapshot"]["original_sha256"] == b["snapshot"]["original_sha256"]
        )
        rows = []
        if comparable:
            prior = {r["id"]: r for r in a["rows"]}
            for row in b["rows"]:
                for stage, state in row["stages"].items():
                    before, after = prior[row["id"]]["stages"][stage]["status"], state["status"]
                    change = (
                        "pending"
                        if "unrecorded" in (before, after)
                        else "unchanged"
                        if before == after
                        else "improved"
                        if after == "found"
                        else "regressed"
                    )
                    rows.append(
                        {
                            "id": row["id"],
                            "label": row["label"],
                            "stage": stage,
                            "before": before,
                            "after": after,
                            "change": change,
                        }
                    )
        return {
            "comparable": comparable,
            "reason": "" if comparable else "原文件、标准版本或评审规则已改变，不能直接比较。",
            "rows": rows,
            "before_seconds": a.get("elapsed_seconds"),
            "after_seconds": b.get("elapsed_seconds"),
            "timing_scope": "local_validation_only",
        }
