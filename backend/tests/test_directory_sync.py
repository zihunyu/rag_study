import pytest
from ragkb.adapters.directory_ledger import SQLiteDirectorySyncLedger
from ragkb.application.directory_sync import DirectorySync
from ragkb.application.worker import LocalIngestionWorker
from ragkb.engineering_security.file_validation import FileValidationError
from ragkb.runtime_components import build_runtime_components


@pytest.fixture
def setup(tmp_path):
    runtime = build_runtime_components(
        storage_root=tmp_path / "storage", database_path=tmp_path / "db.sqlite3"
    )
    root = tmp_path / "sources"
    root.mkdir()
    service = DirectorySync(
        runtime.uploads,
        SQLiteDirectorySyncLedger(tmp_path / "sync.sqlite3"),
        lambda kind: kind + ":v1",
    )
    return runtime, root, service


def test_preview_dedup_then_unchanged_sync_does_not_parse_again(setup):
    runtime, root, service = setup
    for name in ("a.txt", "b.txt"):
        (root / name).write_text("保修期三年，保留购买凭证。", encoding="utf-8")
    preview = service.run(root, runtime.space_id)
    assert preview["created"] == 1 and preview["duplicates"] == 1
    assert runtime.repository.list_documents(runtime.space_id) == []
    first = service.run(root, runtime.space_id, apply=True)
    worker = LocalIngestionWorker(
        runtime.queue,
        runtime.repository,
        runtime.storage,
        runtime.parser_router,
        "test-worker",
        chunker=runtime.chunker,
        indexing_sink=runtime.indexing_sink,
    )
    assert worker.run_once()
    repeat = DirectorySync(
        runtime.uploads, SQLiteDirectorySyncLedger(service.path), service.contract
    ).run(root, runtime.space_id, apply=True)
    assert repeat["reused"] == 1 and repeat["created"] == 0
    assert repeat["results"][0]["job_state"] == "SUCCEEDED"
    assert repeat["results"][0]["document_version_id"] == first["results"][0]["document_version_id"]
    assert not worker.run_once()


def test_changed_file_creates_new_version_and_contract_change_invalidates_reuse(setup):
    runtime, root, service = setup
    source = root / "manual.txt"
    source.write_text("第一版", encoding="utf-8")
    first = service.run(root, runtime.space_id, apply=True)["results"][0]
    source.write_text("第二版", encoding="utf-8")
    second = service.run(root, runtime.space_id, apply=True)
    assert second["updated"] == 1
    assert second["results"][0]["document_id"] == first["document_id"]
    service.contract = lambda kind: kind + ":v2"
    third = service.run(root, runtime.space_id, apply=True)
    assert third["updated"] == 1
    assert len(runtime.repository.get_versions(first["document_id"])) == 3


def test_changed_duplicate_does_not_overwrite_unchanged_alias(setup):
    runtime, root, service = setup
    for name in ("a.txt", "b.txt"):
        (root / name).write_text("same")
    first = service.run(root, runtime.space_id, apply=True)["results"][0]
    (root / "a.txt").write_text("changed")
    result = service.run(root, runtime.space_id, apply=True)
    assert result["created"] == 1 and result["reused"] == 1
    assert len(runtime.repository.get_versions(first["document_id"])) == 1
    rows = {row["path"]: row for row in result["results"]}
    assert rows["b.txt"]["document_id"] == first["document_id"]
    assert rows["a.txt"]["document_id"] != first["document_id"]


def test_rename_reuses_content_and_removal_does_not_delete_document(setup):
    runtime, root, service = setup
    (root / "old.txt").write_text("unchanged")
    first = service.run(root, runtime.space_id, apply=True)["results"][0]
    (root / "old.txt").rename(root / "new.txt")
    result = service.run(root, runtime.space_id, apply=True)
    assert result["reused"] == 1 and result["removed_sources"] == ["old.txt"]
    assert runtime.repository.get_document(first["document_id"])["state"] == "ACTIVE"


def test_resume_after_queue_interruption_uses_same_upload_and_version(setup):
    runtime, root, service = setup
    (root / "a.txt").write_text("unchanged")

    def fail(*args):
        raise RuntimeError("queue interrupted")

    runtime.uploads.before_enqueue = fail
    with pytest.raises(RuntimeError, match="queue interrupted"):
        service.run(root, runtime.space_id, apply=True)
    runtime.uploads.before_enqueue = None
    result = service.run(root, runtime.space_id, apply=True)["results"][0]
    assert len(runtime.repository.get_versions(result["document_id"])) == 1


def test_failed_job_is_reported_and_explicit_retry_creates_one_new_version(setup):
    runtime, root, service = setup
    (root / "a.txt").write_text("unchanged")
    row = service.run(root, runtime.space_id, apply=True)["results"][0]
    job = runtime.queue.lease("test")
    runtime.queue.fail(job.id, "test", "PARSE_FAILED", retryable=False)
    failed = service.run(root, runtime.space_id, apply=True)
    assert failed["failed"] == 1
    resumed = service.run(root, runtime.space_id, apply=True, retry_failed=True)
    assert resumed["updated"] == 1
    repeated = service.run(root, runtime.space_id, apply=True, retry_failed=True)
    assert repeated["reused"] == 1
    assert len(runtime.repository.get_versions(row["document_id"])) == 2


def test_source_changed_after_scan_is_rejected_by_upload_hash(setup, monkeypatch):
    runtime, root, service = setup
    source = root / "a.txt"
    source.write_text("before")
    scan = service.scan

    def changed(path):
        result = scan(path)
        source.write_text("after!")
        return result

    monkeypatch.setattr(service, "scan", changed)
    with pytest.raises(FileValidationError):
        service.run(root, runtime.space_id, apply=True)
    assert runtime.repository.list_documents(runtime.space_id) == []


def test_reverting_content_does_not_reuse_an_obsolete_document_version(setup):
    runtime, root, service = setup
    source = root / "a.txt"
    source.write_text("A")
    first = service.run(root, runtime.space_id, apply=True)["results"][0]
    source.write_text("B")
    service.run(root, runtime.space_id, apply=True)
    source.write_text("A")
    result = service.run(root, runtime.space_id, apply=True)
    assert result["updated"] == 1
    assert len(runtime.repository.get_versions(first["document_id"])) == 3


def test_operational_config_does_not_force_reparsing_but_model_changes_do():
    from ragkb.config.env import EnvSettings

    from scripts.sync_directory import processing_settings

    original = EnvSettings(ocr_enabled=True, ocr_model="vision-v1")
    changed = original.model_copy(
        update={
            "ocr_max_concurrency": 4,
            "ocr_input_cost_per_million_cny": 123,
            "ocr_query_recheck": False,
            "embedding_batch_size": 2,
            "query_embedding_cache_enabled": False,
        }
    )
    assert processing_settings(original) == processing_settings(changed)
    changed = original.model_copy(update={"ocr_model": "vision-v2"})
    assert processing_settings(original) != processing_settings(changed)
