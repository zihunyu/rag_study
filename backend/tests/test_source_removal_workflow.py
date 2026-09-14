import pytest
from ragkb.adapters.directory_ledger import SQLiteDirectorySyncLedger
from ragkb.application.directory_sync import DirectorySync
from ragkb.application.worker import LocalIngestionWorker
from ragkb.infrastructure.directory_removal_service import DirectoryRemovalService
from test_workspace_redesign import publish
from test_workspace_redesign import workspace as workspace


def synced(workspace, tmp_path):
    client, runtime, _, space = workspace
    root = tmp_path / "source"
    root.mkdir()
    (root / "manual.txt").write_text("产品保修期三年，申请需要发票。", encoding="utf-8")
    service = DirectorySync(
        runtime.uploads,
        SQLiteDirectorySyncLedger(runtime.storage.root / "sync/directory.sqlite3"),
        lambda kind: kind,
    )
    record = service.run(root, space, apply=True)["results"][0]
    worker = LocalIngestionWorker(
        runtime.queue,
        runtime.repository,
        runtime.storage,
        runtime.parser_router,
        "removal-test",
        chunker=runtime.chunker,
        indexing_sink=runtime.indexing_sink,
    )
    assert worker.run_once()
    publish(client, record["document_version_id"])
    return root, service, record


def test_removal_enters_durable_review_then_revokes_published_document(workspace, tmp_path):
    client, runtime, _, space = workspace
    root, sync, document = synced(workspace, tmp_path)
    (root / "manual.txt").unlink()
    assert sync.run(root, space)["removal_reviews"]
    assert DirectoryRemovalService(runtime).list(space) == []
    first = sync.run(root, space, apply=True)["removal_reviews"]
    repeated = sync.run(root, space, apply=True)["removal_reviews"]
    assert first == repeated and len(first) == 1
    path = f"/api/spaces/{space}/directory-removals"
    item = client.get(path).json()[0]
    response = client.post(
        path + f"/{item['id']}/review",
        json={"revision": item["revision"], "action": "withdraw", "note": "确认源文件已移除"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "withdrawn"
    lifecycle = runtime.lifecycle_store.documents[document["document_id"]]
    assert str(lifecycle.lifecycle_state) == "REVOKED"
    replay = client.post(
        path + f"/{item['id']}/review",
        json={"revision": item["revision"], "action": "withdraw", "note": "确认源文件已移除"},
    )
    assert replay.json() == response.json()


@pytest.mark.parametrize("resync", [False, True])
def test_restored_source_cannot_be_revoked(workspace, tmp_path, resync):
    client, runtime, _, space = workspace
    root, sync, document = synced(workspace, tmp_path)
    content = (root / "manual.txt").read_bytes()
    (root / "manual.txt").unlink()
    item = sync.run(root, space, apply=True)["removal_reviews"][0]
    (root / "manual.txt").write_bytes(content)
    if resync:
        assert sync.run(root, space, apply=True)["removal_reviews"] == []
        assert DirectoryRemovalService(runtime).list(space)[0]["state"] == "cancelled"
    response = client.post(
        f"/api/spaces/{space}/directory-removals/{item['id']}/review",
        json={"revision": item["revision"], "action": "withdraw", "note": "尝试处理过期待办"},
    )
    assert response.status_code in {412, 422}, response.text
    assert (
        str(runtime.lifecycle_store.documents[document["document_id"]].lifecycle_state) != "REVOKED"
    )


def test_other_directory_alias_prevents_review_until_last_reference_removed(workspace, tmp_path):
    _, _, _, space = workspace
    root, sync, _ = synced(workspace, tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "copy.txt").write_bytes((root / "manual.txt").read_bytes())
    sync.run(other, space, apply=True)
    (root / "manual.txt").unlink()
    assert sync.run(root, space, apply=True)["removal_reviews"] == []
    (other / "copy.txt").unlink()
    assert len(sync.run(other, space, apply=True)["removal_reviews"]) == 1


def test_unavailable_directory_cannot_trigger_withdrawal(workspace, tmp_path):
    client, runtime, _, space = workspace
    root, sync, document = synced(workspace, tmp_path)
    (root / "manual.txt").unlink()
    item = sync.run(root, space, apply=True)["removal_reviews"][0]
    root.rmdir()
    response = client.post(
        f"/api/spaces/{space}/directory-removals/{item['id']}/review",
        json={"revision": item["revision"], "action": "withdraw", "note": "缺失目录需要恢复后重试"},
    )
    assert response.status_code == 422 and "UNAVAILABLE" in response.text
    assert (
        str(runtime.lifecycle_store.documents[document["document_id"]].lifecycle_state) != "REVOKED"
    )
