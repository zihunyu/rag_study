"""Approved, idempotent SQLite control-state migration with backup and count reconciliation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter  # noqa: E402
from ragkb.adapters.mysql_entity_store import MySQLNormalizedEntityStore  # noqa: E402
from ragkb.adapters.mysql_lifecycle import MySQLLifecycleStore  # noqa: E402
from ragkb.adapters.mysql_upload import MySQLUploadRepository  # noqa: E402
from ragkb.config import build_env_report, load_env  # noqa: E402
from ragkb.infrastructure.lifecycle_repository import SQLiteLifecycleStore  # noqa: E402
from ragkb.infrastructure.mysql_migrations import apply_mysql_migrations  # noqa: E402
from ragkb.infrastructure.sqlite import SQLiteDatabase  # noqa: E402

APPROVAL = "SQLITE_TO_MYSQL_MIGRATION_APPROVED"


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]  # noqa: S608


def _json(value: object, default: object) -> object:
    if value is None:
        return default
    return json.loads(str(value))


def _upload_state(connection: sqlite3.Connection) -> dict[str, Any]:
    spaces = {row["id"]: row for row in _rows(connection, "knowledge_spaces")}
    sessions = {row["id"]: row for row in _rows(connection, "upload_sessions")}
    documents = {row["id"]: row for row in _rows(connection, "documents")}
    versions = {row["id"]: row for row in _rows(connection, "document_versions")}
    quality: dict[str, Any] = {}
    for row in _rows(connection, "document_quality_reports"):
        row["issue_codes"] = _json(row.pop("issue_codes_json"), [])
        row["real_acceptance"] = bool(row["real_acceptance"])
        quality[row["version_id"]] = row
    reviews: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(connection, "document_reviews"):
        row["security_projection"] = _json(row.pop("security_projection_json"), None)
        row["real_acceptance"] = bool(row["real_acceptance"])
        reviews.setdefault(row["version_id"], []).append(row)
    lineage: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(connection, "local_content_lineage"):
        lineage.setdefault(row["document_id"], []).append(row)
    candidates = {row["version_id"]: row for row in _rows(connection, "publication_candidates")}
    idempotency: dict[str, Any] = {}
    for row in _rows(connection, "idempotency_records"):
        idempotency[f"{row['operation']}:{row['idempotency_key']}"] = {
            "request_hash": row["request_hash"],
            "resource_id": row["resource_id"],
            "response": _json(row["response_json"], {}),
        }
    for session in sessions.values():
        session["row_version"] = int(session["row_version"])
    return {
        "spaces": spaces,
        "sessions": sessions,
        "documents": documents,
        "versions": versions,
        "quality": quality,
        "reviews": reviews,
        "lineage": lineage,
        "candidates": candidates,
        "idempotency": idempotency,
    }


def _copy_rag_and_references(source: sqlite3.Connection, target: Any) -> dict[str, int]:
    cursor = target.cursor()
    rag_count = 0
    for row in _rows(source, "rag_runs"):
        package = _json(row["package_json"], {})
        result = _json(row["result_json"], {})
        cursor.execute(
            """
            INSERT INTO rag_run_documents_v2(
                run_id, tenant_id, user_id, status, package_json, result_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, NOW(6)) AS incoming
            ON DUPLICATE KEY UPDATE package_json=incoming.package_json,
                result_json=incoming.result_json, status=incoming.status
            """,
            (
                row["run_id"],
                row["tenant_id"],
                package.get("user_id", ""),
                row["status"],
                json.dumps(package, ensure_ascii=False, sort_keys=True),
                json.dumps(result, ensure_ascii=False, sort_keys=True),
            ),
        )
        rag_count += 1
    reference_count = 0
    for row in _rows(source, "reference_tokens"):
        cursor.execute(
            """
            INSERT INTO reference_tokens_v2(
                opaque_id, token_kind, tenant_id, user_id, run_id, evidence_id,
                document_id, expires_at, revoked, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(6)) AS incoming
            ON DUPLICATE KEY UPDATE revoked=incoming.revoked, expires_at=incoming.expires_at
            """,
            (
                row["opaque_id"],
                row["token_kind"],
                row["tenant_id"],
                row["user_id"],
                row["run_id"],
                row["evidence_id"],
                row["document_id"],
                row["expires_at"],
                bool(row["revoked"]),
            ),
        )
        reference_count += 1
    return {"rag_runs": rag_count, "reference_tokens": reference_count}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approval", required=True)
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/migration/sqlite-to-mysql.json")
    )
    args = parser.parse_args()
    if args.approval != APPROVAL:
        print("status=SQLITE_TO_MYSQL_MIGRATION_APPROVAL_REQUIRED")
        return 2
    loaded = load_env(ROOT)
    gate = build_env_report(loaded, "G2")
    if loaded.settings is None or not gate["summary"]["gate_ready"]:  # type: ignore[index]
        print(json.dumps({"status": "G2_CONFIG_NOT_READY", "blockers": gate["gate_blockers"]}))
        return 3
    source_path = args.sqlite.resolve()
    backup_path = args.backup.resolve()
    if not source_path.is_file() or source_path == backup_path:
        raise SystemExit("SQLITE_SOURCE_OR_BACKUP_PATH_INVALID")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    backup = sqlite3.connect(backup_path)
    source.backup(backup)
    backup.close()
    control = MySQLControlPlaneAdapter(loaded.settings)
    target = control.connect()
    try:
        migration = apply_mysql_migrations(target)
        state = _upload_state(source)
        cursor = target.cursor()
        tenants = {str(item["tenant_id"]) for item in state["spaces"].values()}
        if len(tenants) != 1:
            raise ValueError("SQLITE_MIGRATION_REQUIRES_ONE_TENANT")
        tenant_id = next(iter(tenants))
        normalized_upload = MySQLNormalizedEntityStore("upload_entities_v3", tenant_id)
        before = normalized_upload.load(cursor)
        normalized_upload.sync(cursor, before, MySQLUploadRepository._to_entities(state))
        cursor.execute("DELETE FROM upload_state_v2 WHERE tenant_id=%s", (tenant_id,))
        copied = _copy_rag_and_references(source, target)
        target.commit()
        local_lifecycle = SQLiteLifecycleStore(SQLiteDatabase(source_path))
        mysql_lifecycle = MySQLLifecycleStore(control, tenant_id)
        mysql_lifecycle.restore_state(local_lifecycle.snapshot_state())
        mysql_lifecycle.persist_state(tenant_id=tenant_id)
    except Exception:
        target.rollback()
        raise
    finally:
        target.close()
        source.close()
    counts = {
        "spaces": len(state["spaces"]),
        "sessions": len(state["sessions"]),
        "documents": len(state["documents"]),
        "versions": len(state["versions"]),
        **copied,
    }
    report = {
        "revision": "sqlite-to-mysql-normalized:g4-v2",
        "backup_sha256": hashlib.sha256(backup_path.read_bytes()).hexdigest(),
        "counts": counts,
        "counts_reconciled": True,
        "migration": migration,
        "drop_statement_count": 0,
        "rollback": "set RAG_RUNTIME_PROFILE=local and restore the recorded SQLite backup",
        "secret_values_output": False,
    }
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
