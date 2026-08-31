"""Compose G1 local adapters without exposing them to the domain layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.application.uploads import UploadService
from ragkb.config import EnvSettings, build_env_report, find_repository_root, load_env
from ragkb.document_processing.parsers import ParserRouter
from ragkb.engineering_security.file_validation import UploadFileValidator
from ragkb.engineering_security.malware import SignatureMalwareScanner
from ragkb.infrastructure.sqlite import SQLiteDatabase
from ragkb.infrastructure.sqlite_queue import SQLitePersistentJobQueue
from ragkb.infrastructure.upload_repository import SQLiteUploadRepository


@dataclass(frozen=True)
class RuntimeComponents:
    repository_root: Path
    storage: LocalFileStorage
    database: SQLiteDatabase
    repository: SQLiteUploadRepository
    queue: SQLitePersistentJobQueue
    uploads: UploadService
    parser_router: ParserRouter
    tenant_id: str
    space_id: str
    settings: EnvSettings


def build_runtime_components(
    *,
    repository_root: Path | None = None,
    storage_root: Path | None = None,
    database_path: Path | None = None,
) -> RuntimeComponents:
    root = find_repository_root(repository_root)
    loaded = load_env(root)
    report = build_env_report(loaded, "G0")
    if loaded.settings is None or not report["summary"]["gate_ready"]:  # type: ignore[index]
        raise RuntimeError("config/.env has blocking G0 issues; run python scripts/check_env.py")
    settings = loaded.settings
    if storage_root is None:
        configured = settings.local_storage_root
        storage_root = configured if configured.is_absolute() else root / configured
    storage = LocalFileStorage(storage_root)
    storage.ensure_layout()
    configured_database = settings.queue_database_path
    resolved_database = database_path or (
        configured_database if configured_database.is_absolute() else root / configured_database
    )
    database = SQLiteDatabase(resolved_database)
    repository = SQLiteUploadRepository(database)
    queue = SQLitePersistentJobQueue(database)
    tenant_id, space_id = repository.ensure_local_hierarchy(
        settings.auth_local_tenant,
        "general_knowledge",
    )
    validator = UploadFileValidator(max_size_bytes=settings.upload_max_file_size_mb * 1024 * 1024)
    uploads = UploadService(
        repository,
        queue,
        storage,
        validator,
        SignatureMalwareScanner(),
        tenant_id,
        queue_max_attempts=settings.queue_max_retries + 1,
    )
    return RuntimeComponents(
        repository_root=root,
        storage=storage,
        database=database,
        repository=repository,
        queue=queue,
        uploads=uploads,
        parser_router=ParserRouter(),
        tenant_id=tenant_id,
        space_id=space_id,
        settings=settings,
    )
