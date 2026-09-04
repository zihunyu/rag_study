"""SQLite retrieval control-plane adapter retained for local G2 contract tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence

from ragkb.domain.retrieval import (
    AuthorizedChunk,
    RetrievalRelease,
    SearchContext,
    SecurityProjection,
)
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
        self.upsert_chunks((chunk,))

    def upsert_chunks(self, chunks: Sequence[AuthorizedChunk]) -> None:
        if not chunks:
            return
        with self.database.transaction(immediate=True) as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO retrieval_projections(
                    chunk_id, tenant_id, space_id, document_id, document_version_id,
                    parent_chunk_id, display_text, retrieval_text, locator_json,
                    content_checksum, visibility, acl_scope_tokens_json,
                    classification_level, lifecycle_projection, valid_from_epoch,
                    valid_to_epoch, permission_revision, current_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
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
                    )
                    for chunk in chunks
                ],
            )

    def set_document_projection(
        self,
        document_id: str,
        *,
        active_version_id: str | None,
        lifecycle_projection: str,
        permission_revision: int,
    ) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE retrieval_projections
                SET lifecycle_projection = ?, permission_revision = ?,
                    current_version = CASE WHEN document_version_id = ? THEN 1 ELSE 0 END
                WHERE document_id = ?
                """,
                (
                    lifecycle_projection,
                    permission_revision,
                    active_version_id or "",
                    document_id,
                ),
            )
            connection.execute(
                """
                UPDATE local_search_index SET security_watermark = ?
                WHERE chunk_id IN (
                    SELECT chunk_id FROM retrieval_projections WHERE document_id = ?
                )
                """,
                (permission_revision, document_id),
            )

    def delete_document_projection(self, document_id: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "DELETE FROM retrieval_projections WHERE document_id = ?", (document_id,)
            )

    def delete_version_projection(self, document_id: str, version_id: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                DELETE FROM retrieval_projections
                WHERE document_id = ? AND document_version_id = ?
                """,
                (document_id, version_id),
            )

    def set_version_security_projection(
        self, document_id: str, version_id: str, projection: SecurityProjection
    ) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE retrieval_projections
                SET visibility = ?, acl_scope_tokens_json = ?, classification_level = ?,
                    lifecycle_projection = ?, valid_from_epoch = ?, valid_to_epoch = ?,
                    permission_revision = ?, current_version = 0
                WHERE document_id = ? AND document_version_id = ?
                """,
                (
                    projection.visibility,
                    json.dumps(list(projection.acl_scope_tokens), sort_keys=True),
                    projection.classification_level,
                    projection.lifecycle_projection,
                    projection.valid_from_epoch,
                    projection.valid_to_epoch,
                    projection.permission_revision,
                    document_id,
                    version_id,
                ),
            )


class StaticRetrievalReleaseProvider:
    revision = "static-retrieval-release:v1"

    def __init__(self, release: RetrievalRelease) -> None:
        self.release = release

    def current_release(self, tenant_id: str, space_id: str) -> RetrievalRelease:
        if tenant_id != self.release.tenant_id or space_id != self.release.space_id:
            raise KeyError("retrieval release")
        return self.release

    def set_release(self, release: RetrievalRelease) -> None:
        self.release = release


class LocalRetrievalReleaseProvider:
    revision = "local-dynamic-retrieval-release:v1"

    def __init__(
        self,
        *,
        tenant_id: str,
        space_id: str,
        generation_id: str,
        permission_revision: Callable[[], int],
        security_watermark: Callable[[], int],
    ) -> None:
        self.tenant_id = tenant_id
        self.space_id = space_id
        self.generation_id = generation_id
        self.permission_revision = permission_revision
        self.security_watermark = security_watermark
        self._releases: dict[str, RetrievalRelease] = {}

    def current_release(self, tenant_id: str, space_id: str) -> RetrievalRelease:
        if tenant_id != self.tenant_id:
            raise KeyError("retrieval release")
        configured = self._releases.get(space_id)
        if configured is not None:
            return configured
        return RetrievalRelease(
            tenant_id,
            space_id,
            self.generation_id,
            self.permission_revision(),
            self.security_watermark(),
        )

    def set_release(self, release: RetrievalRelease) -> None:
        if release.tenant_id != self.tenant_id:
            raise KeyError("retrieval release")
        self._releases[release.space_id] = release
