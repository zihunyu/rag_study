"""MySQL persistence for RAG packages, results, evidence and feedback."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.domain.ids import new_uuid7
from ragkb.domain.rag import AskResult, Evidence, EvidencePackage, Feedback
from ragkb.infrastructure.rag_repository import _package, _result


class MySQLRAGRunRepository:
    revision = "mysql-rag-run:g4-v2"

    def __init__(self, control: MySQLControlPlaneAdapter) -> None:
        self.control = control

    def save_run(self, package: EvidencePackage, result: AskResult) -> None:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO rag_run_documents_v2(
                    run_id, tenant_id, user_id, status, package_json, result_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, NOW(6))
                """,
                (
                    package.rag_run_id,
                    package.tenant_id,
                    package.user_id,
                    result.status.value,
                    json.dumps(asdict(package), ensure_ascii=False, sort_keys=True),
                    json.dumps(asdict(result), ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _json(self, run_id: str, column: str) -> dict[str, Any] | None:
        if column not in {"package_json", "result_json"}:
            raise ValueError("unsupported RAG JSON column")
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT {column} FROM rag_run_documents_v2 WHERE run_id=%s",  # noqa: S608
                (run_id,),
            )
            row = cursor.fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        value = row[column] if isinstance(row, dict) else row[0]
        loaded = json.loads(value) if isinstance(value, str) else value
        return dict(loaded) if isinstance(loaded, dict) else None

    def get_result(self, run_id: str) -> AskResult | None:
        data = self._json(run_id, "result_json")
        return _result(data) if data is not None else None

    def get_package(self, run_id: str) -> EvidencePackage | None:
        data = self._json(run_id, "package_json")
        return _package(data) if data is not None else None

    def save_feedback(self, feedback: Feedback) -> None:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO rag_feedback_v2(
                    feedback_id, run_id, user_id, feedback_json, created_at
                ) VALUES (%s, %s, %s, %s, NOW(6))
                """,
                (
                    new_uuid7(),
                    feedback.rag_run_id,
                    feedback.user_id,
                    json.dumps(asdict(feedback), ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_evidence(self, run_id: str, evidence_id: str) -> Evidence | None:
        package = self.get_package(run_id)
        if package is None:
            return None
        return next((item for item in package.evidence if item.evidence_id == evidence_id), None)
