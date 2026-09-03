"""SQLite persistence for local observability, pilot, UAT and observation preparation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from ragkb.domain.ids import new_uuid7
from ragkb.domain.uploads import IdempotencyConflictError, OptimisticConcurrencyError
from ragkb.infrastructure.sqlite import SQLiteDatabase


class SQLiteGovernanceRepository:
    revision = "sqlite-governance:g5-g6-v1"

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.database.initialize()

    def record_event(
        self, trace_id: str, event_type: str, severity: str, payload: dict[str, object]
    ) -> str:
        event_id = new_uuid7()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO runtime_events(event_id, trace_id, event_type, severity,
                    payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_id, trace_id, event_type, severity, json.dumps(payload), time.time()),
            )
        return event_id

    def diagnostics(self) -> dict[str, object]:
        with self.database.connect() as connection:
            event_count = int(
                connection.execute("SELECT COUNT(*) AS count FROM runtime_events").fetchone()[
                    "count"
                ]
            )
            severity_rows = connection.execute(
                "SELECT severity, COUNT(*) AS count FROM runtime_events GROUP BY severity"
            ).fetchall()
            queue_rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM job_queue GROUP BY state"
            ).fetchall()
        return {
            "event_count": event_count,
            "events_by_severity": {
                str(row["severity"]): int(row["count"]) for row in severity_rows
            },
            "queue_by_state": {str(row["state"]): int(row["count"]) for row in queue_rows},
            "adapter": "local_stub",
            "otel_export_performed": False,
            "prometheus_export_performed": False,
        }

    def idempotency_response(
        self, tenant_id: str, operation: str, key: str, request_hash: str
    ) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT request_hash, response_json FROM governance_idempotency
                WHERE tenant_id = ? AND operation = ? AND idempotency_key = ?
                """,
                (tenant_id, operation, key),
            ).fetchone()
        if row is None:
            return None
        if str(row["request_hash"]) != request_hash:
            raise IdempotencyConflictError("governance idempotency key conflict")
        payload = json.loads(str(row["response_json"]))
        if not isinstance(payload, dict):
            raise ValueError("GOVERNANCE_IDEMPOTENCY_RESPONSE_INVALID")
        return {str(key): value for key, value in payload.items()}

    def save_idempotency_response(
        self,
        tenant_id: str,
        operation: str,
        key: str,
        request_hash: str,
        response: dict[str, object],
    ) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO governance_idempotency(
                    tenant_id, operation, idempotency_key, request_hash,
                    response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    operation,
                    key,
                    request_hash,
                    json.dumps(response, sort_keys=True),
                    time.time(),
                ),
            )

    def add_evidence(
        self, category: str, revision: str, metadata: dict[str, object]
    ) -> dict[str, object]:
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
        content_hash = hashlib.sha256(encoded.encode(), usedforsecurity=False).hexdigest()
        evidence_id = new_uuid7()
        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM evidence_index WHERE category = ? AND revision = ?",
                (category, revision),
            ).fetchone()
            if existing is not None:
                if str(existing["content_hash"]) != content_hash:
                    raise ValueError("IMMUTABLE_EVIDENCE_REVISION_CONFLICT")
                return dict(existing)
            connection.execute(
                """
                INSERT INTO evidence_index(evidence_id, category, revision, content_hash,
                    metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (evidence_id, category, revision, content_hash, encoded, time.time()),
            )
        return {
            "evidence_id": evidence_id,
            "category": category,
            "revision": revision,
            "content_hash": content_hash,
            "metadata_json": encoded,
        }

    def add_register_record(
        self,
        category: str,
        title: str,
        owner: str,
        state: str,
        revision: str,
        metadata: dict[str, object],
    ) -> dict[str, object]:
        record_id = new_uuid7()
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO governance_register(record_id, category, title, owner, state,
                        revision, metadata_json, simulated, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        record_id,
                        category,
                        title,
                        owner,
                        state,
                        revision,
                        json.dumps(metadata, sort_keys=True),
                        time.time(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("IMMUTABLE_REGISTER_REVISION_CONFLICT") from error
        return {
            "record_id": record_id,
            "category": category,
            "title": title,
            "owner": owner,
            "state": state,
            "revision": revision,
            "metadata": metadata,
            "simulated": True,
        }

    def create_pilot(self, name: str, feature_flag: str) -> dict[str, object]:
        pilot_id = new_uuid7()
        now = time.time()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO pilot_records(pilot_id, name, state, feature_flag, blockers_json,
                    simulated, real_acceptance, revision, created_at, updated_at)
                VALUES (?, ?, 'DRAFT', ?, '[]', 1, 0, 1, ?, ?)
                """,
                (pilot_id, name, feature_flag, now, now),
            )
        return self.get_pilot(pilot_id)

    def get_pilot(self, pilot_id: str) -> dict[str, object]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pilot_records WHERE pilot_id = ?", (pilot_id,)
            ).fetchone()
        if row is None:
            raise KeyError(pilot_id)
        result = dict(row)
        result["blockers"] = json.loads(str(result.pop("blockers_json")))
        return result

    def update_pilot(
        self,
        pilot_id: str,
        state: str,
        blockers: list[str],
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE pilot_records SET state = ?, blockers_json = ?, revision = revision + 1,
                    updated_at = ? WHERE pilot_id = ? AND (? IS NULL OR revision = ?)
                """,
                (
                    state,
                    json.dumps(blockers),
                    time.time(),
                    pilot_id,
                    expected_revision,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                if (
                    connection.execute(
                        "SELECT 1 FROM pilot_records WHERE pilot_id = ?", (pilot_id,)
                    ).fetchone()
                    is None
                ):
                    raise KeyError(pilot_id)
                raise OptimisticConcurrencyError(pilot_id)
        return self.get_pilot(pilot_id)

    def record_canary(
        self,
        pilot_id: str,
        seed: int,
        request_count: int,
        failure_count: int,
        threshold: int,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        run_id = new_uuid7()
        result = "PASS" if failure_count <= threshold else "FAIL"
        with self.database.transaction(immediate=True) as connection:
            if expected_revision is not None:
                cursor = connection.execute(
                    """
                    UPDATE pilot_records SET revision = revision + 1, updated_at = ?
                    WHERE pilot_id = ? AND revision = ?
                    """,
                    (time.time(), pilot_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    raise OptimisticConcurrencyError(pilot_id)
            connection.execute(
                """
                INSERT INTO canary_runs(run_id, pilot_id, seed, request_count,
                    success_count, failure_count, threshold, result, simulated, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    run_id,
                    pilot_id,
                    seed,
                    request_count,
                    request_count - failure_count,
                    failure_count,
                    threshold,
                    result,
                    time.time(),
                ),
            )
        pilot = self.get_pilot(pilot_id)
        return {
            "run_id": run_id,
            "pilot_id": pilot_id,
            "seed": seed,
            "request_count": request_count,
            "success_count": request_count - failure_count,
            "failure_count": failure_count,
            "threshold": threshold,
            "result": result,
            "simulated": True,
            "real_acceptance": False,
            "pilot_revision": int(str(pilot["revision"])),
        }

    def latest_canary(self, pilot_id: str) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM canary_runs WHERE pilot_id = ? ORDER BY created_at DESC LIMIT 1",
                (pilot_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def add_signoff(
        self,
        scope_type: str,
        scope_id: str,
        role: str,
        decision: str,
        signer_id: str,
        comment: str,
    ) -> dict[str, object]:
        signoff_id = new_uuid7()
        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT * FROM governance_signoffs WHERE scope_type = ? AND scope_id = ?
                    AND role = ? AND decision = ? AND signer_id = ? AND comment = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (scope_type, scope_id, role, decision, signer_id, comment),
            ).fetchone()
            if existing is not None:
                item = dict(existing)
                item["simulated"] = bool(item["simulated"])
                return item
            connection.execute(
                """
                INSERT INTO governance_signoffs(signoff_id, scope_type, scope_id, role,
                    decision, signer_id, comment, simulated, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (signoff_id, scope_type, scope_id, role, decision, signer_id, comment, time.time()),
            )
        return {
            "signoff_id": signoff_id,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "role": role,
            "decision": decision,
            "signer_id": signer_id,
            "comment": comment,
            "simulated": True,
        }

    def latest_signoffs(self, scope_type: str, scope_id: str) -> dict[str, str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT role, decision FROM governance_signoffs
                WHERE scope_type = ? AND scope_id = ? ORDER BY created_at
                """,
                (scope_type, scope_id),
            ).fetchall()
        return {str(row["role"]): str(row["decision"]) for row in rows}

    def add_rollout_batch(self, pilot_id: str, ordinal: int, percentage: int) -> dict[str, object]:
        batch_id = new_uuid7()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO rollout_batches(batch_id, pilot_id, ordinal, percentage, state,
                    simulated, created_at) VALUES (?, ?, ?, ?, 'PLANNED', 1, ?)
                """,
                (batch_id, pilot_id, ordinal, percentage, time.time()),
            )
        return {
            "batch_id": batch_id,
            "pilot_id": pilot_id,
            "ordinal": ordinal,
            "percentage": percentage,
            "state": "PLANNED",
            "simulated": True,
        }

    def create_uat_case(
        self, pilot_id: str, title: str, steps: list[str], expected: list[str]
    ) -> dict[str, object]:
        case_id = new_uuid7()
        now = time.time()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO uat_cases(case_id, pilot_id, title, steps_json, expected_json,
                    result, evidence_json, step_results_json, row_version, simulated,
                    created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'NOT_RUN', '[]', '[]', 1, 1, ?, ?)
                """,
                (case_id, pilot_id, title, json.dumps(steps), json.dumps(expected), now, now),
            )
        return self.get_uat_case(case_id)

    def update_uat_case(
        self,
        case_id: str,
        result: str,
        evidence: list[dict[str, str]],
        step_results: list[str],
        expected_row_version: int,
    ) -> dict[str, object]:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE uat_cases SET result = ?, evidence_json = ?, step_results_json = ?,
                    row_version = row_version + 1, updated_at = ?
                WHERE case_id = ? AND row_version = ?
                """,
                (
                    result,
                    json.dumps(evidence),
                    json.dumps(step_results),
                    time.time(),
                    case_id,
                    expected_row_version,
                ),
            )
            if cursor.rowcount != 1:
                if (
                    connection.execute(
                        "SELECT 1 FROM uat_cases WHERE case_id = ?", (case_id,)
                    ).fetchone()
                    is None
                ):
                    raise KeyError(case_id)
                raise OptimisticConcurrencyError(case_id)
        return self.get_uat_case(case_id)

    def get_uat_case(self, case_id: str) -> dict[str, object]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM uat_cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        if row is None:
            raise KeyError(case_id)
        result = dict(row)
        result["steps"] = json.loads(str(result.pop("steps_json")))
        result["expected"] = json.loads(str(result.pop("expected_json")))
        result["evidence"] = json.loads(str(result.pop("evidence_json")))
        result["step_results"] = json.loads(str(result.pop("step_results_json")))
        return result

    def pilot_uat_status(self, pilot_id: str) -> dict[str, object]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT result, evidence_json, steps_json, step_results_json
                FROM uat_cases WHERE pilot_id = ?
                """,
                (pilot_id,),
            ).fetchall()
        valid = bool(rows) and all(
            str(row["result"]) == "PASSED"
            and bool(json.loads(str(row["evidence_json"])))
            and len(json.loads(str(row["steps_json"])))
            == len(json.loads(str(row["step_results_json"])))
            for row in rows
        )
        return {"case_count": len(rows), "all_passed_with_evidence": valid}

    def evidence_reference_exists(self, reference: dict[str, str]) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM evidence_index WHERE category = ? AND revision = ?
                    AND content_hash = ?
                """,
                (
                    reference.get("category", ""),
                    reference.get("revision", ""),
                    reference.get("content_hash", ""),
                ),
            ).fetchone()
        return row is not None

    def add_defect(
        self, scope_type: str, scope_id: str, severity: str, title: str
    ) -> dict[str, object]:
        defect_id = new_uuid7()
        now = time.time()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO governance_defects(defect_id, scope_type, scope_id, severity,
                    title, state, simulated, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'OPEN', 1, ?, ?)
                """,
                (defect_id, scope_type, scope_id, severity, title, now, now),
            )
        return {
            "defect_id": defect_id,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "severity": severity,
            "title": title,
            "state": "OPEN",
            "simulated": True,
            "row_version": 1,
        }

    def open_critical_defects(self, scope_type: str, scope_id: str) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT defect_id FROM governance_defects WHERE scope_type = ? AND scope_id = ?
                    AND severity IN ('P0','P1') AND state != 'RESOLVED'
                """,
                (scope_type, scope_id),
            ).fetchall()
        return [str(row["defect_id"]) for row in rows]

    def resolve_defect(self, defect_id: str, expected_row_version: int) -> dict[str, object]:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE governance_defects SET state = 'RESOLVED', row_version = row_version + 1,
                    updated_at = ? WHERE defect_id = ? AND row_version = ?
                """,
                (time.time(), defect_id, expected_row_version),
            )
            if cursor.rowcount != 1:
                raise OptimisticConcurrencyError(defect_id)
            row = connection.execute(
                "SELECT * FROM governance_defects WHERE defect_id = ?", (defect_id,)
            ).fetchone()
        return dict(row)

    def create_observation(self, name: str, starts_at: float) -> dict[str, object]:
        window_id = new_uuid7()
        ends_at = starts_at + 7 * 24 * 3600
        now = time.time()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO observation_windows(window_id, name, starts_at, ends_at, state,
                    metrics_json, simulated, real_acceptance, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'RUNNING', '{}', 1, 0, ?, ?)
                """,
                (window_id, name, starts_at, ends_at, now, now),
            )
        return self.get_observation(window_id)

    def get_observation(self, window_id: str) -> dict[str, object]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM observation_windows WHERE window_id = ?", (window_id,)
            ).fetchone()
        if row is None:
            raise KeyError(window_id)
        result = dict(row)
        result["metrics"] = json.loads(str(result.pop("metrics_json")))
        return result

    def record_observation_metrics(
        self, window_id: str, metrics: dict[str, float], expected_row_version: int
    ) -> dict[str, object]:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE observation_windows SET metrics_json = ?, row_version = row_version + 1,
                    updated_at = ? WHERE window_id = ? AND row_version = ?
                """,
                (json.dumps(metrics), time.time(), window_id, expected_row_version),
            )
            if cursor.rowcount != 1:
                if (
                    connection.execute(
                        "SELECT 1 FROM observation_windows WHERE window_id = ?", (window_id,)
                    ).fetchone()
                    is None
                ):
                    raise KeyError(window_id)
                raise OptimisticConcurrencyError(window_id)
        return self.get_observation(window_id)

    def close_observation(self, window_id: str, expected_row_version: int) -> dict[str, object]:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE observation_windows SET state = 'CLOSED', row_version = row_version + 1,
                    updated_at = ? WHERE window_id = ? AND row_version = ? AND state = 'RUNNING'
                """,
                (time.time(), window_id, expected_row_version),
            )
            if cursor.rowcount != 1:
                raise OptimisticConcurrencyError(window_id)
        return self.get_observation(window_id)

    def add_incident(self, window_id: str, severity: str, title: str) -> dict[str, object]:
        incident_id = new_uuid7()
        now = time.time()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO incidents(incident_id, window_id, severity, title, state,
                    simulated, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'OPEN', 1, ?, ?)
                """,
                (incident_id, window_id, severity, title, now, now),
            )
        return {
            "incident_id": incident_id,
            "window_id": window_id,
            "severity": severity,
            "title": title,
            "state": "OPEN",
            "simulated": True,
            "row_version": 1,
        }

    def open_critical_incidents(self, window_id: str) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT incident_id FROM incidents WHERE window_id = ?
                    AND severity IN ('P0','P1') AND state != 'RESOLVED'
                """,
                (window_id,),
            ).fetchall()
        return [str(row["incident_id"]) for row in rows]

    def resolve_incident(self, incident_id: str, expected_row_version: int) -> dict[str, object]:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE incidents SET state = 'RESOLVED', row_version = row_version + 1,
                    updated_at = ? WHERE incident_id = ? AND row_version = ?
                """,
                (time.time(), incident_id, expected_row_version),
            )
            if cursor.rowcount != 1:
                raise OptimisticConcurrencyError(incident_id)
            row = connection.execute(
                "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
        return dict(row)
