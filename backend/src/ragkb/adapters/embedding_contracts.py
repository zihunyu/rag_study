"""Immutable generation-to-embedding bindings, shared by API and ingestion workers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.config import EnvSettings
from ragkb.config.vector import vector_collection_name, vector_connection_kwargs
from ragkb.domain.errors import SchemaMismatch


def embedding_contract(settings: EnvSettings) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "endpoint_sha256": hashlib.sha256(
            settings.embedding_base_url.rstrip("/").encode()
        ).hexdigest(),
        "model": settings.embedding_model,
        "model_revision": settings.embedding_model_revision,
        "dimension": settings.embedding_dimension,
        "normalize": settings.embedding_normalize,
        "input_contract": "exact-utf8-provider-output-v1",
        "output_revision": settings.embedding_cache_revision,
    }


def encoded_contract(settings: EnvSettings) -> str:
    return json.dumps(embedding_contract(settings), sort_keys=True, separators=(",", ":"))


def vector_target(settings: EnvSettings) -> str:
    connection = vector_connection_kwargs(settings)
    # Credentials rotate without changing vector identity; never persist them.
    values = {key: connection.get(key) for key in ("uri", "db_name")}
    values["collection"] = vector_collection_name(settings)
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


class EmbeddingContractRegistry:
    """A binding may be inserted once; changing models requires a new generation."""

    def __init__(
        self, *, path: Path | None = None, mysql: MySQLControlPlaneAdapter | None = None
    ) -> None:
        if (path is None) == (mysql is None):
            raise ValueError("EMBEDDING_CONTRACT_STORE_REQUIRED")
        self.path, self.mysql = path, mysql
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS embedding_generation_contracts ("
                    "target_id TEXT NOT NULL, generation_id TEXT NOT NULL, "
                    "contract_json TEXT NOT NULL, "
                    "provenance TEXT NOT NULL, PRIMARY KEY(target_id,generation_id))"
                )

    def get(self, target: str, generation: str) -> dict[str, Any] | None:
        if self.mysql:
            db = self.mysql.connect()
            try:
                cursor = db.cursor()
                cursor.execute(
                    "SELECT contract_json,provenance FROM embedding_generation_contracts "
                    "WHERE target_id=%s AND generation_id=%s",
                    (target, generation),
                )
                row = cursor.fetchone()
            finally:
                db.close()
        else:
            assert self.path is not None
            with closing(sqlite3.connect(self.path)) as db:
                row = db.execute(
                    "SELECT contract_json,provenance FROM embedding_generation_contracts "
                    "WHERE target_id=? AND generation_id=?",
                    (target, generation),
                ).fetchone()
        if row is None:
            return None
        encoded, provenance = (
            (row["contract_json"], row["provenance"]) if isinstance(row, Mapping) else row
        )
        return {
            "contract": json.loads(encoded) if isinstance(encoded, str) else encoded,
            "provenance": provenance,
        }

    def require(self, settings: EnvSettings, generation: str) -> dict[str, Any]:
        row = self.get(vector_target(settings), generation)
        if row is None:
            raise SchemaMismatch("EMBEDDING_CONTRACT_UNREGISTERED")
        if row["contract"] != embedding_contract(settings):
            raise SchemaMismatch("EMBEDDING_CONTRACT_MISMATCH")
        return row

    def bind(self, settings: EnvSettings, generation: str, *, provenance: str) -> None:
        if not provenance.strip() or not generation.strip():
            raise ValueError("EMBEDDING_CONTRACT_PROVENANCE_REQUIRED")
        values = (vector_target(settings), generation, encoded_contract(settings), provenance)
        if self.mysql:
            db = self.mysql.connect()
            try:
                cursor = db.cursor()
                cursor.execute(
                    "INSERT INTO embedding_generation_contracts "
                    "(target_id,generation_id,contract_json,provenance) VALUES (%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE target_id=target_id",
                    values,
                )
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()
        else:
            assert self.path is not None
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute(
                    "INSERT OR IGNORE INTO embedding_generation_contracts VALUES (?,?,?,?)", values
                )
        self.require(settings, generation)
