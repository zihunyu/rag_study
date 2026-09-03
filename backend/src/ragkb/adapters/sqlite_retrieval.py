"""SQLite retrieval control-plane adapter retained for local G2 contract tests."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from ragkb.domain.retrieval import AuthorizedChunk, SearchContext
from ragkb.infrastructure.sqlite import SQLiteDatabase


class SQLiteRetrievalControlPlane:
    revision = "sqlite-retrieval-control:g2-test-v1"

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.database.initialize()

    @staticmethod
    def _chunk(row: Mapping[str, object]) -> AuthorizedChunk:
        locator = json.loads(str(row["locator_json"]))
        acl = json.loads(str(row["acl_scope_tokens_json"]))
        if not isinstance(locator, dict) or not isinstance(acl, list):
            raise ValueError("retrieval projection JSON is invalid")
        return AuthorizedChunk(
            chunk_id=str(row["chunk_id"]),
            tenant_id=str(row["tenant_id"]),
            space_id=str(row["space_id"]),
            document_id=str(row["document_id"]),
            document_version_id=str(row["document_version_id"]),
            parent_chunk_id=(
                str(row["parent_chunk_id"]) if row["parent_chunk_id"] is not None else None
            ),
            display_text=str(row["display_text"]),
            retrieval_text=str(row["retrieval_text"]),
            locator=locator,
            content_checksum=str(row["content_checksum"]),
            visibility="RESTRICTED" if row["visibility"] == "RESTRICTED" else "TENANT",
            acl_scope_tokens=tuple(map(str, acl)),
            classification_level=int(str(row["classification_level"])),
            lifecycle_projection=str(row["lifecycle_projection"]),
            valid_from_epoch=int(str(row["valid_from_epoch"])),
            valid_to_epoch=int(str(row["valid_to_epoch"])),
            permission_revision=int(str(row["permission_revision"])),
            current_version=bool(row["current_version"]),
        )

    @staticmethod
    def _allowed(chunk: AuthorizedChunk, context: SearchContext) -> bool:
        acl_allowed = chunk.visibility == "TENANT" or bool(
            set(chunk.acl_scope_tokens).intersection(context.subject_scope_tokens)
        )
        time_allowed = chunk.valid_from_epoch <= context.as_of_epoch and (
            chunk.valid_to_epoch == 0 or chunk.valid_to_epoch > context.as_of_epoch
        )
        return (
            chunk.tenant_id == context.tenant_id
            and chunk.space_id in context.space_ids
            and chunk.lifecycle_projection == "SERVING"
            and chunk.current_version
            and chunk.classification_level <= context.clearance_level
            and chunk.permission_revision <= context.active_permission_revision
            and time_allowed
            and acl_allowed
        )

    def authorize_chunks(
        self, chunk_ids: Sequence[str], context: SearchContext
    ) -> Mapping[str, AuthorizedChunk]:
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" for _ in chunk_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM retrieval_projections WHERE chunk_id IN ({placeholders})",  # noqa: S608
                tuple(chunk_ids),
            ).fetchall()
        chunks = [self._chunk(dict(row)) for row in rows]
        return {chunk.chunk_id: chunk for chunk in chunks if self._allowed(chunk, context)}

    def authorize_parent(
        self, parent_chunk_id: str, context: SearchContext
    ) -> AuthorizedChunk | None:
        return self.authorize_chunks((parent_chunk_id,), context).get(parent_chunk_id)

    def put_for_test(self, chunk: AuthorizedChunk) -> None:
        """Seed the local adapter only; production control-plane writes use MySQL migrations."""
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO retrieval_projections(
                    chunk_id, tenant_id, space_id, document_id, document_version_id,
                    parent_chunk_id, display_text, retrieval_text, locator_json,
                    content_checksum, visibility, acl_scope_tokens_json,
                    classification_level, lifecycle_projection, valid_from_epoch,
                    valid_to_epoch, permission_revision, current_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.chunk_id,
                    chunk.tenant_id,
                    chunk.space_id,
                    chunk.document_id,
                    chunk.document_version_id,
                    chunk.parent_chunk_id,
                    chunk.display_text,
                    chunk.retrieval_text,
                    json.dumps(chunk.locator, sort_keys=True),
                    chunk.content_checksum,
                    chunk.visibility,
                    json.dumps(chunk.acl_scope_tokens),
                    chunk.classification_level,
                    chunk.lifecycle_projection,
                    chunk.valid_from_epoch,
                    chunk.valid_to_epoch,
                    chunk.permission_revision,
                    int(chunk.current_version),
                ),
            )
