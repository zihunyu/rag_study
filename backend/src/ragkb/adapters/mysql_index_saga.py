"""MySQL index-generation and batch Saga ledger."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter


class MySQLIndexSagaLedger:
    revision = "mysql-index-saga:g4-v1"

    def __init__(self, control: MySQLControlPlaneAdapter) -> None:
        self.control = control

    @staticmethod
    def job_id(generation_id: str, document_version_id: str) -> str:
        digest = hashlib.sha256(f"{generation_id}:{document_version_id}".encode()).hexdigest()
        return f"idx-{digest[:32]}"

    @staticmethod
    def checksum(records: Sequence[Mapping[str, object]]) -> str:
        payload = [(str(record["chunk_id"]), str(record["content_checksum"])) for record in records]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

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
        index_job_id = self.job_id(generation_id, document_version_id)
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO index_jobs_v2(
                    index_job_id, tenant_id, space_id, document_id, document_version_id,
                    generation_id, expected_count, expected_checksum, state, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'BUILDING', NOW(6), NOW(6))
                AS incoming ON DUPLICATE KEY UPDATE
                    expected_count=incoming.expected_count,
                    expected_checksum=incoming.expected_checksum,
                    updated_at=NOW(6)
                """,
                (
                    index_job_id,
                    tenant_id,
                    space_id,
                    document_id,
                    document_version_id,
                    generation_id,
                    len(records),
                    self.checksum(records),
                ),
            )
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
        chunk_ids = [str(record["chunk_id"]) for record in records]
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO index_batches_v2(
                    index_job_id, batch_number, chunk_ids_json, batch_checksum,
                    vector_confirmed, control_confirmed, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, NOW(6)) AS incoming
                ON DUPLICATE KEY UPDATE
                    vector_confirmed=(vector_confirmed OR incoming.vector_confirmed),
                    control_confirmed=(control_confirmed OR incoming.control_confirmed),
                    updated_at=NOW(6)
                """,
                (
                    index_job_id,
                    batch_number,
                    json.dumps(chunk_ids, sort_keys=True),
                    self.checksum(records),
                    vector,
                    control,
                ),
            )
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
                SELECT expected_count,
                       COALESCE(SUM(JSON_LENGTH(chunk_ids_json)), 0) AS confirmed_count,
                       MIN(vector_confirmed) AS all_vectors,
                       MIN(control_confirmed) AS all_control
                FROM index_jobs_v2 jobs
                LEFT JOIN index_batches_v2 batches USING(index_job_id)
                WHERE jobs.index_job_id=%s GROUP BY expected_count
                """,
                (index_job_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(index_job_id)
            value = (
                row
                if isinstance(row, dict)
                else {
                    "expected_count": row[0],
                    "confirmed_count": row[1],
                    "all_vectors": row[2],
                    "all_control": row[3],
                }
            )
            if (
                int(value["expected_count"]) != int(value["confirmed_count"])
                or not bool(value["all_vectors"])
                or not bool(value["all_control"])
            ):
                raise RuntimeError("INDEX_SAGA_RECONCILIATION_NOT_READY")
            cursor.execute(
                """
                UPDATE index_jobs_v2 SET state='READY', error_code=NULL, updated_at=NOW(6)
                WHERE index_job_id=%s AND state='BUILDING'
                """,
                (index_job_id,),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fail(self, index_job_id: str, error_code: str) -> None:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE index_jobs_v2 SET state='FAILED', error_code=%s, updated_at=NOW(6)
                WHERE index_job_id=%s AND state!='READY'
                """,
                (error_code, index_job_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
