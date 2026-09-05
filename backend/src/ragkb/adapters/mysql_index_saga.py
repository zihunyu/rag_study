"""Attempt-aware MySQL index-generation and batch Saga ledger."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter

ChunkManifest = tuple[tuple[str, str], ...]


class MySQLIndexSagaLedger:
    revision = "mysql-index-saga:attempt-manifest:g4-v3"

    def __init__(self, control: MySQLControlPlaneAdapter) -> None:
        self.control = control

    @staticmethod
    def job_id(generation_id: str, document_version_id: str) -> str:
        digest = hashlib.sha256(f"{generation_id}:{document_version_id}".encode()).hexdigest()
        return f"idx-{digest[:32]}"

    @staticmethod
    def manifest(records: Sequence[Mapping[str, object]]) -> ChunkManifest:
        values = tuple(
            sorted(
                (
                    str(record["chunk_id"]),
                    hashlib.sha256(
                        json.dumps(
                            dict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False
                        ).encode()
                    ).hexdigest()
                    if "zilliz_pk" in record
                    else str(record["content_checksum"]),
                )
                for record in records
            )
        )
        if not values:
            raise ValueError("INDEX_SAGA_EMPTY_MANIFEST")
        chunk_ids = [chunk_id for chunk_id, _ in values]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("INDEX_SAGA_DUPLICATE_EXPECTED_CHUNK_ID")
        if any(not chunk_id or len(checksum) != 64 for chunk_id, checksum in values):
            raise ValueError("INDEX_SAGA_MANIFEST_INVALID")
        return values

    @staticmethod
    def _decode_manifest(value: object) -> ChunkManifest:
        loaded = json.loads(value) if isinstance(value, str) else value
        if not isinstance(loaded, list):
            raise ValueError("INDEX_SAGA_MANIFEST_JSON_INVALID")
        parsed: list[tuple[str, str]] = []
        for item in loaded:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError("INDEX_SAGA_MANIFEST_JSON_INVALID")
            parsed.append((str(item[0]), str(item[1])))
        return tuple(sorted(parsed))

    @staticmethod
    def _manifest_json(manifest: ChunkManifest) -> str:
        return json.dumps(manifest, separators=(",", ":"))

    @staticmethod
    def manifest_checksum(manifest: ChunkManifest) -> str:
        return hashlib.sha256(MySQLIndexSagaLedger._manifest_json(manifest).encode()).hexdigest()

    @classmethod
    def checksum(cls, records: Sequence[Mapping[str, object]]) -> str:
        return cls.manifest_checksum(cls.manifest(records))

    @staticmethod
    def _row(row: Any, names: Sequence[str]) -> dict[str, Any]:
        return row if isinstance(row, dict) else dict(zip(names, row, strict=True))

    def begin(
        self,
        *,
        tenant_id: str,
        space_id: str,
        document_id: str,
        document_version_id: str,
        generation_id: str,
        records: Sequence[Mapping[str, object]],
    ) -> str:
        expected_manifest = self.manifest(records)
        expected_checksum = self.manifest_checksum(expected_manifest)
        expected_json = self._manifest_json(expected_manifest)
        index_job_id = self.job_id(generation_id, document_version_id)
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT tenant_id, space_id, document_id, document_version_id, generation_id,
                       expected_count, expected_checksum, expected_manifest_json,
                       state, attempt_number
                FROM index_jobs_v3 WHERE index_job_id=%s FOR UPDATE
                """,
                (index_job_id,),
            )
            raw = cursor.fetchone()
            if raw is None:
                cursor.execute(
                    """
                    INSERT INTO index_jobs_v3(
                        index_job_id, tenant_id, space_id, document_id, document_version_id,
                        generation_id, expected_count, expected_checksum,
                        expected_manifest_json, state, attempt_number, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                              'BUILDING', 1, NOW(6), NOW(6))
                    """,
                    (
                        index_job_id,
                        tenant_id,
                        space_id,
                        document_id,
                        document_version_id,
                        generation_id,
                        len(expected_manifest),
                        expected_checksum,
                        expected_json,
                    ),
                )
                connection.commit()
                return index_job_id
            job = self._row(
                raw,
                (
                    "tenant_id",
                    "space_id",
                    "document_id",
                    "document_version_id",
                    "generation_id",
                    "expected_count",
                    "expected_checksum",
                    "expected_manifest_json",
                    "state",
                    "attempt_number",
                ),
            )
            identity = (
                str(job["tenant_id"]),
                str(job["space_id"]),
                str(job["document_id"]),
                str(job["document_version_id"]),
                str(job["generation_id"]),
            )
            if identity != (tenant_id, space_id, document_id, document_version_id, generation_id):
                raise RuntimeError("INDEX_SAGA_JOB_IDENTITY_CONFLICT")
            same_manifest = bool(
                int(job["expected_count"]) == len(expected_manifest)
                and str(job["expected_checksum"]) == expected_checksum
                and self._decode_manifest(job["expected_manifest_json"]) == expected_manifest
            )
            state = str(job["state"])
            if state == "READY":
                if not same_manifest:
                    raise RuntimeError("INDEX_SAGA_READY_MANIFEST_CONFLICT")
                connection.commit()
                return index_job_id
            if state == "BUILDING" and same_manifest:
                connection.commit()
                return index_job_id
            if state not in {"BUILDING", "FAILED"}:
                raise RuntimeError("INDEX_SAGA_STATE_INVALID")
            cursor.execute("DELETE FROM index_batches_v3 WHERE index_job_id=%s", (index_job_id,))
            cursor.execute(
                """
                UPDATE index_jobs_v3
                SET expected_count=%s, expected_checksum=%s, expected_manifest_json=%s,
                    state='BUILDING', error_code=NULL,
                    attempt_number=attempt_number+1, updated_at=NOW(6)
                WHERE index_job_id=%s AND attempt_number=%s
                """,
                (
                    len(expected_manifest),
                    expected_checksum,
                    expected_json,
                    index_job_id,
                    int(job["attempt_number"]),
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("INDEX_SAGA_BEGIN_CONCURRENT_UPDATE")
            connection.commit()
            return index_job_id
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def confirm_batch(
        self,
        index_job_id: str,
        batch_number: int,
        records: Sequence[Mapping[str, object]],
        *,
        vector: bool,
        control: bool,
    ) -> None:
        if batch_number < 1 or not (vector or control):
            raise ValueError("INDEX_SAGA_BATCH_CONFIRMATION_INVALID")
        manifest = self.manifest(records)
        manifest_json = self._manifest_json(manifest)
        checksum = self.manifest_checksum(manifest)
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT state, attempt_number FROM index_jobs_v3
                WHERE index_job_id=%s FOR UPDATE
                """,
                (index_job_id,),
            )
            raw_job = cursor.fetchone()
            if raw_job is None:
                raise KeyError(index_job_id)
            job = self._row(raw_job, ("state", "attempt_number"))
            state = str(job["state"])
            if state not in {"BUILDING", "READY"}:
                raise RuntimeError("INDEX_SAGA_JOB_NOT_BUILDING")
            cursor.execute(
                """
                SELECT chunk_manifest_json, batch_checksum, vector_confirmed,
                       control_confirmed, attempt_number
                FROM index_batches_v3
                WHERE index_job_id=%s AND batch_number=%s FOR UPDATE
                """,
                (index_job_id, batch_number),
            )
            raw_batch = cursor.fetchone()
            if raw_batch is None:
                if state == "READY":
                    raise RuntimeError("INDEX_SAGA_READY_BATCH_MISSING")
                cursor.execute(
                    """
                    INSERT INTO index_batches_v3(
                        index_job_id, batch_number, attempt_number, chunk_manifest_json,
                        batch_checksum, vector_confirmed, control_confirmed, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(6))
                    """,
                    (
                        index_job_id,
                        batch_number,
                        int(job["attempt_number"]),
                        manifest_json,
                        checksum,
                        vector,
                        control,
                    ),
                )
            else:
                batch = self._row(
                    raw_batch,
                    (
                        "chunk_manifest_json",
                        "batch_checksum",
                        "vector_confirmed",
                        "control_confirmed",
                        "attempt_number",
                    ),
                )
                if (
                    int(batch["attempt_number"]) != int(job["attempt_number"])
                    or str(batch["batch_checksum"]) != checksum
                    or self._decode_manifest(batch["chunk_manifest_json"]) != manifest
                ):
                    raise RuntimeError("INDEX_SAGA_BATCH_MANIFEST_CONFLICT")
                cursor.execute(
                    """
                    UPDATE index_batches_v3
                    SET vector_confirmed=%s, control_confirmed=%s, updated_at=NOW(6)
                    WHERE index_job_id=%s AND batch_number=%s AND attempt_number=%s
                    """,
                    (
                        bool(batch["vector_confirmed"]) or vector,
                        bool(batch["control_confirmed"]) or control,
                        index_job_id,
                        batch_number,
                        int(job["attempt_number"]),
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("INDEX_SAGA_BATCH_CONCURRENT_UPDATE")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_ready(self, index_job_id: str) -> None:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT expected_count, expected_checksum, expected_manifest_json,
                       state, attempt_number
                FROM index_jobs_v3 WHERE index_job_id=%s FOR UPDATE
                """,
                (index_job_id,),
            )
            raw_job = cursor.fetchone()
            if raw_job is None:
                raise KeyError(index_job_id)
            job = self._row(
                raw_job,
                (
                    "expected_count",
                    "expected_checksum",
                    "expected_manifest_json",
                    "state",
                    "attempt_number",
                ),
            )
            state = str(job["state"])
            if state not in {"BUILDING", "READY"}:
                raise RuntimeError("INDEX_SAGA_JOB_NOT_BUILDING")
            cursor.execute(
                """
                SELECT batch_number, attempt_number, chunk_manifest_json, batch_checksum,
                       vector_confirmed, control_confirmed
                FROM index_batches_v3 WHERE index_job_id=%s ORDER BY batch_number
                FOR UPDATE
                """,
                (index_job_id,),
            )
            batches = [
                self._row(
                    row,
                    (
                        "batch_number",
                        "attempt_number",
                        "chunk_manifest_json",
                        "batch_checksum",
                        "vector_confirmed",
                        "control_confirmed",
                    ),
                )
                for row in cursor.fetchall()
            ]
            if [int(batch["batch_number"]) for batch in batches] != list(
                range(1, len(batches) + 1)
            ):
                raise RuntimeError("INDEX_SAGA_BATCH_SEQUENCE_INCOMPLETE")
            combined: list[tuple[str, str]] = []
            for batch in batches:
                manifest = self._decode_manifest(batch["chunk_manifest_json"])
                if (
                    int(batch["attempt_number"]) != int(job["attempt_number"])
                    or self.manifest_checksum(manifest) != str(batch["batch_checksum"])
                    or not bool(batch["vector_confirmed"])
                    or not bool(batch["control_confirmed"])
                ):
                    raise RuntimeError("INDEX_SAGA_BATCH_RECONCILIATION_FAILED")
                combined.extend(manifest)
            chunk_ids = [chunk_id for chunk_id, _ in combined]
            if len(chunk_ids) != len(set(chunk_ids)):
                raise RuntimeError("INDEX_SAGA_DUPLICATE_CONFIRMED_CHUNK_ID")
            actual_manifest = tuple(sorted(combined))
            expected_manifest = self._decode_manifest(job["expected_manifest_json"])
            if (
                len(actual_manifest) != int(job["expected_count"])
                or actual_manifest != expected_manifest
                or self.manifest_checksum(actual_manifest) != str(job["expected_checksum"])
            ):
                raise RuntimeError("INDEX_SAGA_EXPECTED_MANIFEST_MISMATCH")
            if state == "READY":
                connection.commit()
                return
            cursor.execute(
                """
                UPDATE index_jobs_v3 SET state='READY', error_code=NULL, updated_at=NOW(6)
                WHERE index_job_id=%s AND state='BUILDING' AND attempt_number=%s
                """,
                (index_job_id, int(job["attempt_number"])),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("INDEX_SAGA_READY_CONCURRENT_UPDATE")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def is_ready(self, index_job_id: str) -> bool:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT state FROM index_jobs_v3 WHERE index_job_id=%s", (index_job_id,))
            row = cursor.fetchone()
            if row is None:
                raise KeyError(index_job_id)
            return str(row["state"] if isinstance(row, dict) else row[0]) == "READY"
        finally:
            connection.close()

    def fail(self, index_job_id: str, error_code: str) -> None:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE index_jobs_v3 SET state='FAILED', error_code=%s, updated_at=NOW(6)
                WHERE index_job_id=%s AND state='BUILDING'
                """,
                (error_code, index_job_id),
            )
            if cursor.rowcount != 1:
                cursor.execute(
                    "SELECT state FROM index_jobs_v3 WHERE index_job_id=%s", (index_job_id,)
                )
                row = cursor.fetchone()
                if row is None:
                    raise KeyError(index_job_id)
                state = str(row["state"] if isinstance(row, dict) else row[0])
                if state not in {"FAILED", "READY"}:
                    raise RuntimeError("INDEX_SAGA_FAIL_CONCURRENT_UPDATE")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
