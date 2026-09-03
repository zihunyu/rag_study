"""MySQL-authoritative governance aggregate for production."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.domain.ids import new_uuid7
from ragkb.domain.uploads import IdempotencyConflictError, OptimisticConcurrencyError


def _empty() -> dict[str, Any]:
    return {
        "events": [],
        "evidence": {},
        "register": {},
        "pilots": {},
        "canaries": {},
        "signoffs": [],
        "rollouts": {},
        "uat": {},
        "defects": {},
        "observations": {},
        "incidents": {},
        "idempotency": {},
    }


class MySQLGovernanceRepository:
    revision = "mysql-governance:g5-g6-v1"

    def __init__(self, control: MySQLControlPlaneAdapter, tenant_id: str) -> None:
        self.control = control
        self.tenant_id = tenant_id

    def _load(self, cursor: Any, *, locked: bool = False) -> dict[str, Any]:
        statement = (
            "SELECT state_json FROM governance_state_v2 WHERE tenant_id=%s FOR UPDATE"
            if locked
            else "SELECT state_json FROM governance_state_v2 WHERE tenant_id=%s"
        )
        cursor.execute(statement, (self.tenant_id,))
        row = cursor.fetchone()
        if row is None:
            return _empty()
        value = row["state_json"] if isinstance(row, dict) else row[0]
        loaded = json.loads(value) if isinstance(value, str) else value
        if not isinstance(loaded, dict):
            raise ValueError("MYSQL_GOVERNANCE_STATE_INVALID")
        return loaded

    def _mutate[Result](self, callback: Callable[[dict[str, Any]], Result]) -> Result:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            state = self._load(cursor, locked=True)
            result = callback(state)
            cursor.execute(
                """
                INSERT INTO governance_state_v2(tenant_id, state_json, updated_at)
                VALUES (%s, %s, NOW(6)) AS incoming
                ON DUPLICATE KEY UPDATE state_json=incoming.state_json, updated_at=NOW(6)
                """,
                (self.tenant_id, json.dumps(state, ensure_ascii=False, sort_keys=True)),
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _read(self) -> dict[str, Any]:
        connection = self.control.connect()
        try:
            return self._load(connection.cursor())
        finally:
            connection.close()

    def record_event(
        self, trace_id: str, event_type: str, severity: str, payload: dict[str, object]
    ) -> str:
        event_id = new_uuid7()
        self._mutate(
            lambda state: state["events"].append(
                {
                    "event_id": event_id,
                    "trace_id": trace_id,
                    "event_type": event_type,
                    "severity": severity,
                    "payload": payload,
                    "created_at": time.time(),
                }
            )
        )
        return event_id

    def diagnostics(self) -> dict[str, object]:
        events = self._read()["events"]
        return {
            "event_count": len(events),
            "events_by_severity": dict(Counter(item["severity"] for item in events)),
            "queue_by_state": {},
            "adapter": self.revision,
            "otel_export_performed": False,
            "prometheus_export_performed": False,
        }

    def idempotency_response(
        self, tenant_id: str, operation: str, key: str, request_hash: str
    ) -> dict[str, object] | None:
        item = self._read()["idempotency"].get(f"{tenant_id}:{operation}:{key}")
        if item is None:
            return None
        if item["request_hash"] != request_hash:
            raise IdempotencyConflictError("governance idempotency key conflict")
        return dict(item["response"])

    def save_idempotency_response(
        self,
        tenant_id: str,
        operation: str,
        key: str,
        request_hash: str,
        response: dict[str, object],
    ) -> None:
        def mutate(state: dict[str, Any]) -> None:
            identity = f"{tenant_id}:{operation}:{key}"
            existing = state["idempotency"].get(identity)
            if existing is not None and existing["request_hash"] != request_hash:
                raise IdempotencyConflictError("governance idempotency key conflict")
            state["idempotency"][identity] = {
                "request_hash": request_hash,
                "response": response,
            }

        self._mutate(mutate)

    def add_evidence(
        self, category: str, revision: str, metadata: dict[str, object]
    ) -> dict[str, object]:
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
        content_hash = hashlib.sha256(encoded.encode()).hexdigest()

        def mutate(state: dict[str, Any]) -> dict[str, object]:
            identity = f"{category}:{revision}"
            existing = state["evidence"].get(identity)
            if existing is not None:
                if existing["content_hash"] != content_hash:
                    raise ValueError("IMMUTABLE_EVIDENCE_REVISION_CONFLICT")
                return dict(existing)
            item: dict[str, object] = {
                "evidence_id": new_uuid7(),
                "category": category,
                "revision": revision,
                "content_hash": content_hash,
                "metadata_json": encoded,
            }
            state["evidence"][identity] = item
            return item

        return self._mutate(mutate)

    def add_register_record(
        self,
        category: str,
        title: str,
        owner: str,
        state_value: str,
        revision: str,
        metadata: dict[str, object],
    ) -> dict[str, object]:
        def mutate(state: dict[str, Any]) -> dict[str, object]:
            identity = f"{category}:{revision}"
            if identity in state["register"]:
                raise ValueError("IMMUTABLE_REGISTER_REVISION_CONFLICT")
            item = {
                "record_id": new_uuid7(),
                "category": category,
                "title": title,
                "owner": owner,
                "state": state_value,
                "revision": revision,
                "metadata": metadata,
                "simulated": True,
            }
            state["register"][identity] = item
            return item

        return self._mutate(mutate)

    def create_pilot(self, name: str, feature_flag: str) -> dict[str, object]:
        pilot_id = new_uuid7()
        now = time.time()

        def mutate(state: dict[str, Any]) -> dict[str, object]:
            item = {
                "pilot_id": pilot_id,
                "name": name,
                "state": "DRAFT",
                "feature_flag": feature_flag,
                "blockers": [],
                "simulated": True,
                "real_acceptance": False,
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
            state["pilots"][pilot_id] = item
            return item

        return self._mutate(mutate)

    def get_pilot(self, pilot_id: str) -> dict[str, object]:
        item = self._read()["pilots"].get(pilot_id)
        if item is None:
            raise KeyError(pilot_id)
        return dict(item)

    def update_pilot(
        self,
        pilot_id: str,
        state_value: str,
        blockers: list[str],
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        def mutate(state: dict[str, Any]) -> dict[str, object]:
            item = state["pilots"].get(pilot_id)
            if item is None:
                raise KeyError(pilot_id)
            if expected_revision is not None and item["revision"] != expected_revision:
                raise OptimisticConcurrencyError(pilot_id)
            item.update(
                state=state_value,
                blockers=blockers,
                revision=int(item["revision"]) + 1,
                updated_at=time.time(),
            )
            return dict(item)

        return self._mutate(mutate)

    def record_canary(
        self,
        pilot_id: str,
        seed: int,
        request_count: int,
        failure_count: int,
        threshold: int,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        def mutate(state: dict[str, Any]) -> dict[str, object]:
            pilot = state["pilots"].get(pilot_id)
            if pilot is None:
                raise KeyError(pilot_id)
            if expected_revision is not None and pilot["revision"] != expected_revision:
                raise OptimisticConcurrencyError(pilot_id)
            pilot["revision"] = int(pilot["revision"]) + 1
            item = {
                "run_id": new_uuid7(),
                "pilot_id": pilot_id,
                "seed": seed,
                "request_count": request_count,
                "success_count": request_count - failure_count,
                "failure_count": failure_count,
                "threshold": threshold,
                "result": "PASS" if failure_count <= threshold else "FAIL",
                "simulated": True,
                "real_acceptance": False,
                "pilot_revision": pilot["revision"],
                "created_at": time.time(),
            }
            state["canaries"].setdefault(pilot_id, []).append(item)
            return item

        return self._mutate(mutate)

    def latest_canary(self, pilot_id: str) -> dict[str, object] | None:
        values = self._read()["canaries"].get(pilot_id, [])
        return dict(values[-1]) if values else None

    def add_signoff(
        self,
        scope_type: str,
        scope_id: str,
        role: str,
        decision: str,
        signer_id: str,
        comment: str,
    ) -> dict[str, object]:
        def mutate(state: dict[str, Any]) -> dict[str, object]:
            for item in reversed(state["signoffs"]):
                if all(
                    item[key] == value
                    for key, value in {
                        "scope_type": scope_type,
                        "scope_id": scope_id,
                        "role": role,
                        "decision": decision,
                        "signer_id": signer_id,
                        "comment": comment,
                    }.items()
                ):
                    return dict(item)
            item = {
                "signoff_id": new_uuid7(),
                "scope_type": scope_type,
                "scope_id": scope_id,
                "role": role,
                "decision": decision,
                "signer_id": signer_id,
                "comment": comment,
                "simulated": True,
                "created_at": time.time(),
            }
            state["signoffs"].append(item)
            return item

        return self._mutate(mutate)

    def latest_signoffs(self, scope_type: str, scope_id: str) -> dict[str, str]:
        return {
            str(item["role"]): str(item["decision"])
            for item in self._read()["signoffs"]
            if item["scope_type"] == scope_type and item["scope_id"] == scope_id
        }

    def add_rollout_batch(self, pilot_id: str, ordinal: int, percentage: int) -> dict[str, object]:
        def mutate(state: dict[str, Any]) -> dict[str, object]:
            item = {
                "batch_id": new_uuid7(),
                "pilot_id": pilot_id,
                "ordinal": ordinal,
                "percentage": percentage,
                "state": "PLANNED",
                "simulated": True,
            }
            state["rollouts"][item["batch_id"]] = item
            return item

        return self._mutate(mutate)

    def create_uat_case(
        self, pilot_id: str, title: str, steps: list[str], expected: list[str]
    ) -> dict[str, object]:
        now = time.time()

        def mutate(state: dict[str, Any]) -> dict[str, object]:
            item = {
                "case_id": new_uuid7(),
                "pilot_id": pilot_id,
                "title": title,
                "steps": steps,
                "expected": expected,
                "result": "NOT_RUN",
                "evidence": [],
                "step_results": [],
                "row_version": 1,
                "simulated": True,
                "created_at": now,
                "updated_at": now,
            }
            state["uat"][item["case_id"]] = item
            return item

        return self._mutate(mutate)

    def get_uat_case(self, case_id: str) -> dict[str, object]:
        item = self._read()["uat"].get(case_id)
        if item is None:
            raise KeyError(case_id)
        return dict(item)

    def update_uat_case(
        self,
        case_id: str,
        result: str,
        evidence: list[dict[str, str]],
        step_results: list[str],
        expected_row_version: int,
    ) -> dict[str, object]:
        def mutate(state: dict[str, Any]) -> dict[str, object]:
            item = state["uat"].get(case_id)
            if item is None:
                raise KeyError(case_id)
            if item["row_version"] != expected_row_version:
                raise OptimisticConcurrencyError(case_id)
            item.update(
                result=result,
                evidence=evidence,
                step_results=step_results,
                row_version=int(item["row_version"]) + 1,
                updated_at=time.time(),
            )
            return dict(item)

        return self._mutate(mutate)

    def pilot_uat_status(self, pilot_id: str) -> dict[str, object]:
        values = [item for item in self._read()["uat"].values() if item["pilot_id"] == pilot_id]
        valid = bool(values) and all(
            item["result"] == "PASSED"
            and bool(item["evidence"])
            and len(item["steps"]) == len(item["step_results"])
            for item in values
        )
        return {"case_count": len(values), "all_passed_with_evidence": valid}

    def evidence_reference_exists(self, reference: dict[str, str]) -> bool:
        identity = f"{reference.get('category', '')}:{reference.get('revision', '')}"
        item = self._read()["evidence"].get(identity)
        return bool(item and item["content_hash"] == reference.get("content_hash"))

    def add_defect(
        self, scope_type: str, scope_id: str, severity: str, title: str
    ) -> dict[str, object]:
        def mutate(state: dict[str, Any]) -> dict[str, object]:
            item = {
                "defect_id": new_uuid7(),
                "scope_type": scope_type,
                "scope_id": scope_id,
                "severity": severity,
                "title": title,
                "state": "OPEN",
                "simulated": True,
                "row_version": 1,
            }
            state["defects"][item["defect_id"]] = item
            return item

        return self._mutate(mutate)

    def open_critical_defects(self, scope_type: str, scope_id: str) -> list[str]:
        return [
            str(item["defect_id"])
            for item in self._read()["defects"].values()
            if item["scope_type"] == scope_type
            and item["scope_id"] == scope_id
            and item["severity"] in {"P0", "P1"}
            and item["state"] != "RESOLVED"
        ]

    def resolve_defect(self, defect_id: str, expected_row_version: int) -> dict[str, object]:
        return self._resolve("defects", "defect_id", defect_id, expected_row_version)

    def create_observation(self, name: str, starts_at: float) -> dict[str, object]:
        now = time.time()

        def mutate(state: dict[str, Any]) -> dict[str, object]:
            item = {
                "window_id": new_uuid7(),
                "name": name,
                "starts_at": starts_at,
                "ends_at": starts_at + 7 * 24 * 3600,
                "state": "RUNNING",
                "metrics": {},
                "simulated": True,
                "real_acceptance": False,
                "row_version": 1,
                "created_at": now,
                "updated_at": now,
            }
            state["observations"][item["window_id"]] = item
            return item

        return self._mutate(mutate)

    def get_observation(self, window_id: str) -> dict[str, object]:
        item = self._read()["observations"].get(window_id)
        if item is None:
            raise KeyError(window_id)
        return dict(item)

    def record_observation_metrics(
        self, window_id: str, metrics: dict[str, float], expected_row_version: int
    ) -> dict[str, object]:
        def mutate(state: dict[str, Any]) -> dict[str, object]:
            item = state["observations"].get(window_id)
            if item is None:
                raise KeyError(window_id)
            if item["row_version"] != expected_row_version:
                raise OptimisticConcurrencyError(window_id)
            item.update(
                metrics=metrics,
                row_version=int(item["row_version"]) + 1,
                updated_at=time.time(),
            )
            return dict(item)

        return self._mutate(mutate)

    def close_observation(self, window_id: str, expected_row_version: int) -> dict[str, object]:
        def mutate(state: dict[str, Any]) -> dict[str, object]:
            item = state["observations"].get(window_id)
            if item is None or item["state"] != "RUNNING":
                raise OptimisticConcurrencyError(window_id)
            if item["row_version"] != expected_row_version:
                raise OptimisticConcurrencyError(window_id)
            item.update(
                state="CLOSED",
                row_version=int(item["row_version"]) + 1,
                updated_at=time.time(),
            )
            return dict(item)

        return self._mutate(mutate)

    def add_incident(self, window_id: str, severity: str, title: str) -> dict[str, object]:
        def mutate(state: dict[str, Any]) -> dict[str, object]:
            item = {
                "incident_id": new_uuid7(),
                "window_id": window_id,
                "severity": severity,
                "title": title,
                "state": "OPEN",
                "simulated": True,
                "row_version": 1,
            }
            state["incidents"][item["incident_id"]] = item
            return item

        return self._mutate(mutate)

    def open_critical_incidents(self, window_id: str) -> list[str]:
        return [
            str(item["incident_id"])
            for item in self._read()["incidents"].values()
            if item["window_id"] == window_id
            and item["severity"] in {"P0", "P1"}
            and item["state"] != "RESOLVED"
        ]

    def resolve_incident(self, incident_id: str, expected_row_version: int) -> dict[str, object]:
        return self._resolve("incidents", "incident_id", incident_id, expected_row_version)

    def _resolve(
        self, collection: str, id_field: str, item_id: str, expected_row_version: int
    ) -> dict[str, object]:
        def mutate(state: dict[str, Any]) -> dict[str, object]:
            item = state[collection].get(item_id)
            if item is None or item["row_version"] != expected_row_version:
                raise OptimisticConcurrencyError(item_id)
            item.update(state="RESOLVED", row_version=int(item["row_version"]) + 1)
            return dict(item)

        return self._mutate(mutate)
