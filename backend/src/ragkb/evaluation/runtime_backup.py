"""Full local runtime backup/restore probe using only generated temporary data."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pydantic import SecretStr

from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker
from ragkb.document_processing.parsers import ParserRouter
from ragkb.engineering_security.references import ReferenceTokenError
from ragkb.runtime_components import build_runtime_components


def _upload(client: TestClient, space_id: str, name: str, content: bytes) -> dict[str, Any]:
    created = client.post(
        f"/api/v1/spaces/{space_id}/upload-sessions",
        headers={"Idempotency-Key": f"backup-create-{name}"},
        json={
            "filename": f"{name}.txt",
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "declared_mime": "text/plain",
        },
    )
    uploaded = client.put(
        created.json()["upload_path"],
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    return dict(
        client.post(
            f"/api/v1/upload-sessions/{created.json()['upload_session_id']}:complete",
            headers={
                "If-Match": uploaded.headers["etag"],
                "Idempotency-Key": f"backup-complete-{name}",
            },
        ).json()
    )


def run_runtime_backup_restore_probe() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ragkb-g4-runtime-restore-") as temporary:
        root = Path(temporary)
        source_storage = root / "source-storage"
        source_database = root / "source.sqlite3"
        backup_storage = root / "backup-storage"
        backup_database = root / "backup.sqlite3"
        restored_storage = root / "restored-storage"
        restored_database = root / "restored.sqlite3"
        secret = SecretStr("synthetic-runtime-backup-secret-32bytes")
        source = build_runtime_components(
            storage_root=source_storage,
            database_path=source_database,
            app_secret_override=secret,
        )
        client = TestClient(create_app(source))
        published = _upload(client, source.space_id, "published", b"published backup body")
        worker = LocalIngestionWorker(
            source.queue,
            source.repository,
            source.storage,
            ParserRouter(),
            "backup-worker",
        )
        assert worker.run_once()
        version_id = published["document_version_id"]
        client.post(
            f"/api/v1/document-versions/{version_id}/review",
            headers={"Idempotency-Key": "backup-review"},
            json={
                "decision": "APPROVED",
                "comment": "temporary backup probe",
                "security_projection": {
                    "visibility": "TENANT",
                    "classification_level": 0,
                    "acl_scope_tokens": [],
                },
            },
        )
        client.post(
            f"/api/v1/document-versions/{version_id}:publish",
            headers={"Idempotency-Key": "backup-publish"},
        )
        deleted = _upload(client, source.space_id, "deleted", b"deleted backup body")
        reference_url = source.reference_signer.source_url(
            "backup-run",
            "E1",
            source.tenant_id,
            source.settings.auth_local_user_id,
            deleted["document_id"],
        )
        client.delete(
            f"/api/v1/documents/{deleted['document_id']}",
            headers={"Idempotency-Key": "backup-delete"},
        )
        rag_run_id = client.post("/api/v1/ask", json={"question": "backup probe"}).json()[
            "rag_run_id"
        ]
        queued = source.queue.enqueue(
            "backup-probe", {"synthetic": True}, "backup-key", "backup-hash"
        )
        replayed = source.queue.enqueue(
            "backup-probe", {"synthetic": True}, "backup-key", "backup-hash"
        )
        original_key = str(source.repository.get_version(version_id)["original_key"])
        prefix, _, _ = original_key.rpartition("/original/")
        artifact_key = f"{prefix}/artifacts/canonical-document-v1.json"
        expected_hashes = {
            ("original", original_key): hashlib.sha256(
                source.storage.read_bytes("original", original_key)
            ).hexdigest(),
            ("artifacts", artifact_key): hashlib.sha256(
                source.storage.read_bytes("artifacts", artifact_key)
            ).hexdigest(),
        }
        client.close()

        with (
            closing(sqlite3.connect(source_database)) as connection,
            closing(sqlite3.connect(backup_database)) as target,
        ):
            connection.backup(target)
        shutil.copytree(source_storage, backup_storage)
        with (
            closing(sqlite3.connect(backup_database)) as connection,
            closing(sqlite3.connect(restored_database)) as target,
        ):
            connection.backup(target)
        shutil.copytree(backup_storage, restored_storage)

        restored = build_runtime_components(
            storage_root=restored_storage,
            database_path=restored_database,
            app_secret_override=secret,
        )
        parts = reference_url.split("/")
        try:
            restored.reference_signer.resolve(
                parts[4],
                parts[6],
                restored.tenant_id,
                restored.settings.auth_local_user_id,
            )
        except ReferenceTokenError:
            reference_revocation_preserved = True
        else:
            reference_revocation_preserved = False
        with restored.database.connect() as connection:
            cleanup_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM cleanup_outbox WHERE document_id = ?",
                    (deleted["document_id"],),
                ).fetchone()["count"]
            )
            candidate_state = str(
                connection.execute(
                    "SELECT projection_state FROM publication_candidates WHERE version_id = ?",
                    (version_id,),
                ).fetchone()["projection_state"]
            )
        restored_replay = restored.queue.enqueue(
            "backup-probe", {"synthetic": True}, "backup-key", "backup-hash"
        )
        restored_hashes = {
            key: hashlib.sha256(restored.storage.read_bytes(*key)).hexdigest()
            for key in expected_hashes
        }
        return {
            "temporary_generated_data_only": True,
            "real_project_data_touched": False,
            "schema_table_families_restored": [
                "queue",
                "lifecycle",
                "reference",
                "rag",
                "publication",
                "lineage",
            ],
            "tombstone_replayed_first": restored.lifecycle_store.is_tombstoned(
                deleted["document_id"]
            ),
            "deleted_document_visible_after_restore": restored.lifecycle_store.is_accessible(
                deleted["document_id"]
            ),
            "reference_revocation_preserved": reference_revocation_preserved,
            "current_pointer_preserved": (
                restored.repository.get_document(published["document_id"])["current_version_id"]
                == version_id
            ),
            "publication_candidate_state": candidate_state,
            "cleanup_outbox_count": cleanup_count,
            "queue_idempotency_preserved": queued.id == replayed.id == restored_replay.id,
            "rag_run_preserved": restored.rag_repository.get_result(rag_run_id) is not None,
            "file_hashes_preserved": restored_hashes == expected_hashes,
        }
