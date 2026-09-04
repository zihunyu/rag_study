"""Production MySQL retrieval projection, lifecycle, and release adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.domain.errors import ProviderUnavailable, SchemaMismatch
from ragkb.domain.retrieval import (
    AuthorizedChunk,
    RetrievalRelease,
    SearchContext,
    SecurityProjection,
)


class MySQLRetrievalControlPlane:
    revision = "mysql-retrieval-control:v1"

    def __init__(self, control: MySQLControlPlaneAdapter) -> None:
        self.control = control

    @staticmethod
    def _chunk(row: Mapping[str, Any]) -> AuthorizedChunk:
        locator = row["locator_json"]
        acl = row["acl_scope_tokens_json"]
        if isinstance(locator, str):
            locator = json.loads(locator)
        if isinstance(acl, str):
            acl = json.loads(acl)
        if not isinstance(locator, dict) or not isinstance(acl, list):
            raise SchemaMismatch("MYSQL_RETRIEVAL_PROJECTION_JSON_INVALID")
        return AuthorizedChunk(
            chunk_id=str(row["chunk_id"]),
            tenant_id=str(row["tenant_id"]),
            space_id=str(row["space_id"]),
            document_id=str(row["document_id"]),
            document_version_id=str(row["document_version_id"]),
            parent_chunk_id=str(row["parent_chunk_id"]) if row["parent_chunk_id"] else None,
            display_text=str(row["display_text"]),
            retrieval_text=str(row["retrieval_text"]),
            locator=locator,
            content_checksum=str(row["content_checksum"]),
            visibility="RESTRICTED" if row["visibility"] == "RESTRICTED" else "TENANT",
            acl_scope_tokens=tuple(map(str, acl)),
            classification_level=int(row["classification_level"]),
            lifecycle_projection=str(row["lifecycle_projection"]),
            valid_from_epoch=int(row["valid_from_epoch"]),
            valid_to_epoch=int(row["valid_to_epoch"]),
            permission_revision=int(row["permission_revision"]),
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
        return bool(
            chunk.tenant_id == context.tenant_id
            and chunk.space_id in context.space_ids
            and chunk.lifecycle_projection == "SERVING"
            and chunk.current_version
            and chunk.classification_level <= context.clearance_level
            and chunk.permission_revision <= context.active_permission_revision
            and time_allowed
            and acl_allowed
        )

    def _fetch_all(self, statement: str, parameters: Sequence[object]) -> list[Mapping[str, Any]]:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(statement, tuple(parameters))
            rows = cursor.fetchall()
            if rows and not isinstance(rows[0], Mapping):
                names = [item[0] for item in cursor.description]
                return [dict(zip(names, row, strict=True)) for row in rows]
            return list(rows)
        except (OSError, TimeoutError) as error:
            raise ProviderUnavailable("MYSQL_RETRIEVAL_UNAVAILABLE") from error
        finally:
            connection.close()

    def authorize_chunks(
        self, chunk_ids: Sequence[str], context: SearchContext
    ) -> Mapping[str, AuthorizedChunk]:
        if not chunk_ids:
            return {}
        placeholders = ",".join("%s" for _ in chunk_ids)
        rows = self._fetch_all(
            f"""
            SELECT * FROM retrieval_chunk_projections
            WHERE tenant_id = %s AND space_id IN ({",".join("%s" for _ in context.space_ids)})
              AND chunk_id IN ({placeholders})
            """,  # noqa: S608 - placeholders only, values remain parameterized
            (context.tenant_id, *context.space_ids, *chunk_ids),
        )
        chunks = (self._chunk(row) for row in rows)
        return {chunk.chunk_id: chunk for chunk in chunks if self._allowed(chunk, context)}

    def authorize_parent(
        self, parent_chunk_id: str, context: SearchContext
    ) -> AuthorizedChunk | None:
        return self.authorize_chunks((parent_chunk_id,), context).get(parent_chunk_id)

    def upsert_chunks(self, chunks: Sequence[AuthorizedChunk]) -> None:
        if not chunks:
            return
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.executemany(
                """
                INSERT INTO retrieval_chunk_projections(
                    chunk_id, tenant_id, space_id, document_id, document_version_id,
                    parent_chunk_id, display_text, retrieval_text, locator_json,
                    content_checksum, visibility, acl_scope_tokens_json,
                    classification_level, lifecycle_projection, valid_from_epoch,
                    valid_to_epoch, permission_revision, current_version, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(6)
                ) AS incoming
                ON DUPLICATE KEY UPDATE
                    tenant_id=incoming.tenant_id, space_id=incoming.space_id,
                    document_id=incoming.document_id,
                    document_version_id=incoming.document_version_id,
                    parent_chunk_id=incoming.parent_chunk_id,
                    display_text=incoming.display_text, retrieval_text=incoming.retrieval_text,
                    locator_json=incoming.locator_json,
                    content_checksum=incoming.content_checksum, visibility=incoming.visibility,
                    acl_scope_tokens_json=incoming.acl_scope_tokens_json,
                    classification_level=incoming.classification_level,
                    lifecycle_projection=incoming.lifecycle_projection,
                    valid_from_epoch=incoming.valid_from_epoch,
                    valid_to_epoch=incoming.valid_to_epoch,
                    permission_revision=incoming.permission_revision,
                    current_version=incoming.current_version, updated_at=NOW(6)
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
                        chunk.current_version,
                    )
                    for chunk in chunks
                ],
            )
            connection.commit()
        except (OSError, TimeoutError) as error:
            connection.rollback()
            raise ProviderUnavailable("MYSQL_PROJECTION_WRITE_UNAVAILABLE") from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def set_document_projection(
        self,
        document_id: str,
        *,
        active_version_id: str | None,
        lifecycle_projection: str,
        permission_revision: int,
    ) -> None:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE retrieval_chunk_projections
                SET lifecycle_projection=%s, permission_revision=%s,
                    current_version=(document_version_id=%s), updated_at=NOW(6)
                WHERE document_id=%s
                """,
                (
                    lifecycle_projection,
                    permission_revision,
                    active_version_id or "",
                    document_id,
                ),
            )
            cursor.execute(
                """
                UPDATE retrieval_release_state release_state
                JOIN (
                    SELECT DISTINCT tenant_id, space_id
                    FROM retrieval_chunk_projections WHERE document_id=%s
                ) projection_scope
                  ON release_state.tenant_id=projection_scope.tenant_id
                 AND release_state.space_id=projection_scope.space_id
                SET release_state.active_permission_revision=GREATEST(
                        release_state.active_permission_revision, %s
                    ),
                    release_state.security_watermark=GREATEST(
                        release_state.security_watermark, %s
                    ),
                    release_state.updated_at=NOW(6)
                """,
                (document_id, permission_revision, permission_revision),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_document_projection(self, document_id: str) -> None:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "DELETE FROM retrieval_chunk_projections WHERE document_id=%s", (document_id,)
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_version_projection(self, document_id: str, version_id: str) -> None:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                DELETE FROM retrieval_chunk_projections
                WHERE document_id=%s AND document_version_id=%s
                """,
                (document_id, version_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def set_version_security_projection(
        self, document_id: str, version_id: str, projection: SecurityProjection
    ) -> None:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE retrieval_chunk_projections
                SET visibility=%s, acl_scope_tokens_json=%s, classification_level=%s,
                    lifecycle_projection=%s, valid_from_epoch=%s, valid_to_epoch=%s,
                    permission_revision=%s, current_version=FALSE, updated_at=NOW(6)
                WHERE document_id=%s AND document_version_id=%s
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
            if cursor.rowcount < 1:
                raise KeyError("SECURITY_PROJECTION_TARGET_NOT_INDEXED")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def document_projection_exists(self, document_id: str) -> bool:
        rows = self._fetch_all(
            "SELECT chunk_id FROM retrieval_chunk_projections WHERE document_id=%s LIMIT 1",
            (document_id,),
        )
        return bool(rows)

    def current_release(self, tenant_id: str, space_id: str) -> RetrievalRelease:
        rows = self._fetch_all(
            """
            SELECT tenant_id, space_id, active_generation_id,
                   active_permission_revision, security_watermark
            FROM retrieval_release_state WHERE tenant_id=%s AND space_id=%s
            """,
            (tenant_id, space_id),
        )
        if len(rows) != 1:
            raise SchemaMismatch("MYSQL_RETRIEVAL_RELEASE_MISSING")
        row = rows[0]
        return RetrievalRelease(
            str(row["tenant_id"]),
            str(row["space_id"]),
            str(row["active_generation_id"]),
            int(row["active_permission_revision"]),
            int(row["security_watermark"]),
        )

    def set_release(self, release: RetrievalRelease) -> None:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO retrieval_release_state(
                    tenant_id, space_id, active_generation_id,
                    active_permission_revision, security_watermark, updated_at
                ) VALUES (%s, %s, %s, %s, %s, NOW(6)) AS incoming
                ON DUPLICATE KEY UPDATE
                    active_generation_id=incoming.active_generation_id,
                    active_permission_revision=incoming.active_permission_revision,
                    security_watermark=incoming.security_watermark,
                    updated_at=NOW(6)
                """,
                (
                    release.tenant_id,
                    release.space_id,
                    release.active_generation_id,
                    release.active_permission_revision,
                    release.security_watermark,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
