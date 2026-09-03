from __future__ import annotations

from pathlib import Path

from ragkb.adapters.zilliz import (
    ZILLIZ_REQUIRED_FIELDS,
    ZillizCloudAdapter,
    ZillizSafeProjectionWriter,
    build_zilliz_filter,
)
from ragkb.config import load_env
from ragkb.domain.retrieval import SearchContext


def test_zilliz_adapter_uses_uri_token_database_without_exposing_token(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    secret = "ZILLIZ_SECRET_MUST_NOT_LEAK"  # noqa: S105
    env_file.write_text(
        "\n".join(
            (
                "ZILLIZ_CLOUD_URI=https://cluster.cn-north.vectordb.zilliz.com.cn:19530",
                f"ZILLIZ_CLOUD_TOKEN={secret}",
                "ZILLIZ_CLOUD_DATABASE=default",
            )
        ),
        encoding="utf-8",
    )
    loaded = load_env(Path(__file__).resolve().parents[2], env_path=env_file, environ={})
    assert loaded.settings is not None
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return object()

    adapter = ZillizCloudAdapter(loaded.settings, client_factory=factory)
    adapter.connect()

    assert captured["uri"].startswith("https://")
    assert captured["token"] == secret
    assert captured["db_name"] == "default"
    assert secret not in str(adapter.safe_status())
    assert adapter.safe_status()["real_connection_attempted"] is True
    assert adapter.safe_status()["mutating_call_performed"] is False


def _context() -> SearchContext:
    return SearchContext(
        tenant_id="tenant-1",
        space_ids=("space-1",),
        subject_scope_tokens=("group:reader",),
        clearance_level=2,
        as_of_epoch=1_800_000_000,
        active_generation_id="generation-1",
        active_permission_revision=12,
        required_security_watermark=10,
    )


class _ReadOnlyClient:
    def __init__(self, *, collection_exists: bool = True) -> None:
        self.collection_exists = collection_exists
        self.calls: list[str] = []

    def list_databases(self, **kwargs):
        self.calls.append("list_databases")
        return ["default"]

    def list_collections(self, **kwargs):
        self.calls.append("list_collections")
        return ["rag_chunks"] if self.collection_exists else []

    def has_collection(self, **kwargs):
        self.calls.append("has_collection")
        return self.collection_exists

    def describe_collection(self, **kwargs):
        self.calls.append("describe_collection")
        fields = [{"name": name, "params": {}} for name in ZILLIZ_REQUIRED_FIELDS]
        for field in fields:
            if field["name"] == "dense_vector":
                field["params"] = {"dim": 1024}
            if field["name"] == "retrieval_text":
                field["enable_analyzer"] = True
        return {"fields": fields, "functions": [{"type": 1}]}

    def search(self, **kwargs):
        self.calls.append("search")
        return [
            [
                {
                    "distance": 0.9,
                    "entity": {
                        "chunk_id": "chunk-1",
                        "document_version_id": "version-1",
                        "parent_chunk_id": "parent-1",
                    },
                }
            ]
        ]


def test_zilliz_read_only_inspection_never_mutates_and_reports_missing_collection(
    tmp_path: Path,
) -> None:
    loaded = load_env(
        Path(__file__).resolve().parents[2], env_path=tmp_path / "missing", environ={}
    )
    assert loaded.settings is not None
    existing = _ReadOnlyClient()
    adapter = ZillizCloudAdapter(loaded.settings, client_factory=lambda **kwargs: existing)

    report = adapter.read_only_inspect()

    assert report["schema_compatible"] is True
    assert report["mutating_call_performed"] is False
    assert existing.calls == [
        "list_databases",
        "list_collections",
        "has_collection",
        "describe_collection",
    ]
    missing = _ReadOnlyClient(collection_exists=False)
    blocked = ZillizCloudAdapter(
        loaded.settings, client_factory=lambda **kwargs: missing
    ).read_only_inspect()
    assert blocked["zilliz_collection_create_approval_required"] is True
    assert missing.calls == ["list_databases", "list_collections", "has_collection"]


def test_default_database_session_is_usable_when_database_list_returns_internal_ids(
    tmp_path: Path,
) -> None:
    loaded = load_env(
        Path(__file__).resolve().parents[2], env_path=tmp_path / "missing", environ={}
    )
    assert loaded.settings is not None
    client = _ReadOnlyClient(collection_exists=False)

    def internal_ids(**kwargs):
        client.calls.append("list_databases")
        return ["db_internal_opaque_id"]

    client.list_databases = internal_ids  # type: ignore[method-assign]
    report = ZillizCloudAdapter(
        loaded.settings, client_factory=lambda **kwargs: client
    ).read_only_inspect()

    assert report["database_exists"] is True
    assert report["database_session_usable"] is True
    assert report["database_list_contains_configured_name"] is False
    assert report["collection_exists"] is False
    assert report["collection_count"] == 0
    assert report["capacity_available_under_last_observed_limit"] is True


def test_zilliz_filter_and_search_contract_include_security_constraints(tmp_path: Path) -> None:
    loaded = load_env(
        Path(__file__).resolve().parents[2], env_path=tmp_path / "missing", environ={}
    )
    assert loaded.settings is not None
    client = _ReadOnlyClient()
    adapter = ZillizCloudAdapter(loaded.settings, client_factory=lambda **kwargs: client)
    expression = build_zilliz_filter(_context())

    bm25 = adapter.search_bm25("保修期", _context(), 5)
    dense = adapter.search_dense([0.0] * 1024, _context(), 5)

    for required in (
        "tenant_id",
        "space_id",
        "index_generation_id",
        "lifecycle_projection",
        "classification_level",
        "permission_revision",
        "valid_from_epoch",
        "acl_scope_tokens",
    ):
        assert required in expression
    assert bm25[0].channel == "bm25"
    assert dense[0].channel == "dense"
    assert bm25[0].chunk_id == "chunk-1"
    assert adapter.observed_security_watermark(_context()) == 0


def test_production_projection_writer_never_uses_multi_entity_batch(tmp_path: Path) -> None:
    loaded = load_env(
        Path(__file__).resolve().parents[2], env_path=tmp_path / "missing", environ={}
    )
    assert loaded.settings is not None

    class _WriterClient:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        def insert(self, **kwargs):
            self.batch_sizes.append(len(kwargs["data"]))
            return {"insert_count": 1}

    client = _WriterClient()
    writer = ZillizSafeProjectionWriter(client, loaded.settings)

    inserted = writer.insert_records(({"zilliz_pk": "one"}, {"zilliz_pk": "two"}))

    assert inserted == ("one", "two")
    assert writer.safe_batch_size == 1
    assert client.batch_sizes == [1, 1]
