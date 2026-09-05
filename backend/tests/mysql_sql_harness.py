"""Transactional SQL harness for repository behavior, NOT a MySQL integration claim.

Only placeholder/time syntax is adapted. Real storage, uniqueness, rollback and
UPDATE rowcounts are exercised using an isolated SQLite database.
"""

import re
import sqlite3


class SQLControl:
    def __init__(self, path):
        self.path = path
        self.statements = []
        self.fail_match = None
        with sqlite3.connect(path) as connection:
            for kind in ("upload", "governance", "lifecycle"):
                connection.execute(
                    f"CREATE TABLE {kind}_state_v2(tenant_id TEXT PRIMARY KEY, state_json TEXT)"
                )
                connection.execute(f"""CREATE TABLE {kind}_entities_v3(
                    tenant_id TEXT, entity_type TEXT, entity_id TEXT, logical_key TEXT,
                    parent_id TEXT, ordinal INTEGER, payload_json TEXT, entity_revision INTEGER,
                    created_at TEXT, updated_at TEXT, PRIMARY KEY(tenant_id,
                    entity_type,
                    entity_id))""")
            connection.executescript("""
                CREATE TABLE rag_run_documents_v2(run_id TEXT PRIMARY KEY,
                    tenant_id TEXT,
                    user_id TEXT,
                    status TEXT,
                    package_json TEXT,
                    result_json TEXT,
                    created_at TEXT);
                CREATE TABLE rag_feedback_v2(feedback_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    user_id TEXT,
                    feedback_json TEXT,
                    created_at TEXT);
                CREATE TABLE reference_tokens_v2(opaque_id TEXT PRIMARY KEY,
                    token_kind TEXT,
                    tenant_id TEXT,
                    user_id TEXT,
                    run_id TEXT,
                    evidence_id TEXT,
                    document_id TEXT,
                    expires_at REAL,
                    revoked INTEGER,
                    created_at TEXT);
                CREATE TABLE publication_outbox_v3(tenant_id TEXT, operation TEXT,
                    idempotency_key TEXT,
                    document_id TEXT,
                    target_version_id TEXT,
                    generation_id TEXT,
                    state TEXT,
                    attempt_count INTEGER,
                    payload_json TEXT,
                    error_code TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    PRIMARY KEY(tenant_id,
                    operation,
                    idempotency_key));
                CREATE TABLE retrieval_release_state(tenant_id TEXT, space_id TEXT,
                    active_generation_id TEXT,
                    active_permission_revision INTEGER,
                    security_watermark INTEGER,
                    updated_at TEXT,
                    PRIMARY KEY(tenant_id,
                    space_id));
            """)

    def connect(self):
        return SQLConnection(self)


class SQLConnection:
    def __init__(self, control):
        self.control = control
        self.connection = sqlite3.connect(control.path)
        self.connection.create_function("JSON_UNQUOTE", 1, lambda value: value)
        self.connection.row_factory = lambda cursor, row: dict(
            zip([c[0] for c in cursor.description], row, strict=True)
        )

    def cursor(self):
        return SQLCursor(self)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


class SQLCursor:
    def __init__(self, owner):
        self.owner = owner
        self.cursor = owner.connection.cursor()

    @property
    def description(self):
        return self.cursor.description

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def execute(self, sql, parameters=()):
        self.owner.control.statements.append((sql, parameters))
        if self.owner.control.fail_match and self.owner.control.fail_match in sql:
            raise ConnectionError("injected database write failure")
        sql = (
            sql.replace("%s", "?").replace("NOW(6)", "CURRENT_TIMESTAMP").replace("FOR UPDATE", "")
        )
        sql = re.sub(r"AS incoming\s+ON DUPLICATE KEY UPDATE", "ON CONFLICT DO UPDATE SET", sql)
        sql = sql.replace("incoming.", "excluded.")
        return self.cursor.execute(sql, parameters)

    def fetchall(self):
        return self.cursor.fetchall()

    def fetchone(self):
        return self.cursor.fetchone()
