"""Deterministic SQLite publication readiness; performs no real vector-store calls."""

from __future__ import annotations

from ragkb.contracts.lifecycle import PublicationReadiness
from ragkb.infrastructure.sqlite import SQLiteDatabase


class SQLitePublicationReadiness:
    revision = "sqlite-publication-readiness:g3-v1"

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def check(
        self, document_id: str, version_id: str, *, rollback: bool = False
    ) -> PublicationReadiness:
        with self.database.connect() as connection:
            document = connection.execute(
                "SELECT row_version FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            version = connection.execute(
                """
                SELECT processing_state, content_sha256 FROM document_versions
                WHERE id = ? AND document_id = ?
                """,
                (version_id, document_id),
            ).fetchone()
            candidate = connection.execute(
                "SELECT * FROM publication_candidates WHERE version_id = ?",
                (version_id,),
            ).fetchone()
            quality = connection.execute(
                "SELECT * FROM document_quality_reports WHERE version_id = ?",
                (version_id,),
            ).fetchone()
            review = connection.execute(
                """
                SELECT * FROM document_reviews WHERE version_id = ?
                ORDER BY created_at DESC, review_id DESC LIMIT 1
                """,
                (version_id,),
            ).fetchone()
        row_version = int(document["row_version"]) if document is not None else -1
        checksum = str(version["content_sha256"]) if version is not None else ""
        generation = str(candidate["generation_id"]) if candidate is not None else ""
        projection_state = str(candidate["projection_state"]) if candidate is not None else ""
        required = int(candidate["required_watermark"]) if candidate is not None else 0
        observed = int(candidate["observed_watermark"]) if candidate is not None else 0
        observed_checksum = str(candidate["observed_checksum"]) if candidate is not None else ""
        error_code: str | None = None
        if document is None or version is None:
            error_code = "PUBLICATION_TARGET_NOT_FOUND"
        elif str(version["processing_state"]) != "VALIDATED":
            error_code = "PUBLICATION_VERSION_NOT_VALIDATED"
        elif quality is None:
            error_code = "PUBLICATION_QUALITY_REPORT_MISSING"
        elif str(quality["disposition"]) == "BLOCKED_REAL_VALIDATION":
            error_code = "PUBLICATION_QUALITY_BLOCKED_REAL_VALIDATION"
        elif review is None:
            error_code = "PUBLICATION_REVIEW_REQUIRED"
        elif str(review["decision"]) != "APPROVED":
            error_code = "PUBLICATION_REVIEW_NOT_APPROVED"
        elif review["security_revision"] is None or review["security_projection_json"] is None:
            error_code = "PUBLICATION_SECURITY_REVIEW_REQUIRED"
        elif str(review["quality_revision"]) != str(quality["parser_revision"]):
            error_code = "PUBLICATION_REVIEW_REVISION_MISMATCH"
        elif candidate is None:
            error_code = "PUBLICATION_CANDIDATE_MISSING"
        elif projection_state != ("RETIRED" if rollback else "STAGED"):
            error_code = "PUBLICATION_PROJECTION_NOT_STAGED"
        elif generation != f"local-generation:{version_id}":
            error_code = "PUBLICATION_GENERATION_MISMATCH"
        elif observed < required:
            error_code = "PUBLICATION_WATERMARK_NOT_READY"
        elif str(candidate["expected_checksum"]) != checksum or observed_checksum != checksum:
            error_code = "PUBLICATION_CHECKSUM_MISMATCH"
        return PublicationReadiness(
            ready=error_code is None,
            document_id=document_id,
            version_id=version_id,
            generation_id=generation,
            projection_state=projection_state,
            required_watermark=required,
            observed_watermark=observed,
            expected_checksum=checksum,
            observed_checksum=observed_checksum,
            document_row_version=row_version,
            error_code=error_code,
        )
