from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.adapters.mysql_upload import MySQLUploadRepository
from ragkb.config import load_env
from ragkb.domain.retrieval import SearchContext
from test_mysql_retrieval import _chunk, _Connection


@pytest.mark.parametrize(
    "change",
    [
        {"acl_scope_tokens": ("group:other",)},
        {"classification_level": 2},
        {"valid_from_epoch": 101},
        {"valid_to_epoch": 100},
        {"lifecycle_projection": "STAGED"},
        {"current_version": False},
        {"permission_revision": 3},
        {"tenant_id": "other"},
        {"space_id": "other"},
        {"index_generation_id": "retired-generation"},
        {"document_version_id": "another-version"},
        {},
    ],
)
def test_mysql_chunk_reads_enforce_retrieval_policy(tmp_path, monkeypatch, change):
    chunk = replace(_chunk(), **change)
    row = asdict(chunk)
    row["locator_json"] = json.dumps(row.pop("locator"))
    row["acl_scope_tokens_json"] = json.dumps(row.pop("acl_scope_tokens"))
    connection = _Connection([row], {})
    settings = load_env(
        Path(__file__).resolve().parents[2], env_path=tmp_path / "missing", environ={}
    ).settings
    assert settings is not None
    control = MySQLControlPlaneAdapter(settings, connection_factory=lambda **kwargs: connection)
    repository = MySQLUploadRepository(control, "tenant-1", "generation-1")
    monkeypatch.setattr(repository, "_read", lambda: {"versions": {"version-1": {}}})
    context = SearchContext(
        "tenant-1", ("space-1",), ("group:reader",), 1, 100, "generation-1", 2, 2
    )
    try:
        with pytest.raises(ValueError, match="CHUNK_READ_CONTEXT_REQUIRED"):
            repository.list_chunks("version-1")
        rows = repository.list_chunks("version-1", context=context, limit=5, offset=3)
        assert bool(rows) is (not change)
        if rows:
            assert rows[0]["text"] == chunk.display_text
        assert repository.list_chunks("version-1", preview=True)
        assert any(
            params == ("version-1", "tenant-1", "generation-1", -1, -1, "", 6, 3)
            for _, params in connection.statements
        )
    finally:
        control.close()
