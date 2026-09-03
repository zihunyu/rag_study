"""SQLite local G3 RAG run, evidence and feedback repository."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any

from ragkb.domain.ids import new_uuid7
from ragkb.domain.rag import (
    AnswerStatus,
    AskResult,
    Citation,
    Evidence,
    EvidencePackage,
    Feedback,
    QuestionDisposition,
)
from ragkb.infrastructure.sqlite import SQLiteDatabase


def _package(data: dict[str, Any]) -> EvidencePackage:
    return EvidencePackage(
        **{
            **data,
            "evidence": tuple(Evidence(**item) for item in data["evidence"]),
            "disposition": QuestionDisposition(data["disposition"]),
        }
    )


def _result(data: dict[str, Any]) -> AskResult:
    return AskResult(
        **{
            **data,
            "status": AnswerStatus(data["status"]),
            "citations": tuple(Citation(**item) for item in data["citations"]),
            "evidence": tuple(Evidence(**item) for item in data["evidence"]),
            "warnings": tuple(data["warnings"]),
        }
    )


class SQLiteRAGRunRepository:
    revision = "sqlite-rag-run:g3-v1"

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.database.initialize()

    def save_run(self, package: EvidencePackage, result: AskResult) -> None:
        package_json = json.dumps(asdict(package), ensure_ascii=False, sort_keys=True)
        result_json = json.dumps(asdict(result), ensure_ascii=False, sort_keys=True)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO rag_runs(
                    run_id, tenant_id, query, status, package_json, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    package.rag_run_id,
                    package.tenant_id,
                    package.query,
                    result.status.value,
                    package_json,
                    result_json,
                    time.time(),
                ),
            )
            for evidence in package.evidence:
                connection.execute(
                    """
                    INSERT INTO rag_evidence(run_id, evidence_id, evidence_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        package.rag_run_id,
                        evidence.evidence_id,
                        json.dumps(asdict(evidence), ensure_ascii=False, sort_keys=True),
                    ),
                )

    def _row_json(self, run_id: str, column: str) -> dict[str, Any] | None:
        if column not in {"package_json", "result_json"}:
            raise ValueError("unsupported RAG run JSON column")
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT {column} FROM rag_runs WHERE run_id = ?",  # noqa: S608
                (run_id,),
            ).fetchone()
        return json.loads(str(row[column])) if row is not None else None

    def get_result(self, run_id: str) -> AskResult | None:
        data = self._row_json(run_id, "result_json")
        return _result(data) if data is not None else None

    def get_package(self, run_id: str) -> EvidencePackage | None:
        data = self._row_json(run_id, "package_json")
        return _package(data) if data is not None else None

    def save_feedback(self, feedback: Feedback) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO user_feedback(
                    id, run_id, user_id, rating, reason_code, comment,
                    index_generation_id, retrieval_revision, prompt_revision,
                    model_revision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_uuid7(),
                    feedback.rag_run_id,
                    feedback.user_id,
                    feedback.rating,
                    feedback.reason_code,
                    feedback.comment,
                    feedback.index_generation_id,
                    feedback.retrieval_revision,
                    feedback.prompt_revision,
                    feedback.model_revision,
                    time.time(),
                ),
            )

    def get_evidence(self, run_id: str, evidence_id: str) -> Evidence | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT evidence_json FROM rag_evidence
                WHERE run_id = ? AND evidence_id = ?
                """,
                (run_id, evidence_id),
            ).fetchone()
        return Evidence(**json.loads(str(row["evidence_json"]))) if row is not None else None
