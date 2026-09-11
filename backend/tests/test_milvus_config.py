from __future__ import annotations

# These credentials are deliberately synthetic test fixtures.
# ruff: noqa: S105, S106
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from pymilvus import MilvusClient
from ragkb.adapters.zilliz import MilvusHybridAdapter
from ragkb.adapters.zilliz_lifecycle_probe import synthetic_records
from ragkb.adapters.zilliz_provision import CREATE_APPROVAL, provision_and_validate
from ragkb.adapters.zilliz_readiness import request_collection_load_if_needed
from ragkb.adapters.zilliz_schema import build_sdk_schema
from ragkb.config import EnvSettings, build_env_report, load_env
from ragkb.config.report import conditional_issues
from ragkb.config.vector import normalize_milvus_uri, vector_connection_kwargs
from ragkb.infrastructure.zilliz_plan import build_zilliz_collection_plan


def _root(tmp_path: Path, main: str, overlay: str = "") -> Path:
    config = tmp_path / "config"
    config.mkdir()
    (config / ".env.example").write_text("", encoding="utf8")
    (config / ".env").write_text(main, encoding="utf8")
    (config / ".env.milvus").write_text(overlay, encoding="utf8")
    return tmp_path


@pytest.mark.parametrize("backend", ["zilliz", "milvus"])
def test_selected_overlay_and_process_precedence(tmp_path: Path, backend: str) -> None:
    root = _root(
        tmp_path,
        f"VECTOR_BACKEND={backend}\nVECTOR_URI=base:19530\n",
        "VECTOR_URI=standalone:19530\nVECTOR_USER=root\nVECTOR_PASSWORD=fixture-pass\n",
    )
    loaded = load_env(root, environ={})
    assert loaded.settings is not None
    assert loaded.settings.vector_uri == (
        "standalone:19530" if backend == "milvus" else "base:19530"
    )
    process = load_env(root, environ={"VECTOR_BACKEND": "milvus", "VECTOR_URI": "other:19530"})
    assert process.settings is not None
    assert process.settings.vector_uri == "other:19530"
    assert process.sources["VECTOR_URI"] == "process_environment"
    report = build_env_report(process)
    assert "fixture-pass" not in json.dumps(report)
    assert next(row for row in report["variables"] if row["name"] == "VECTOR_PASSWORD")["secret"]
    assert process.sources["VECTOR_PASSWORD"] == "milvus_env"
    explicit = load_env(root, env_path=root / "config/.env", environ={})
    assert explicit.settings is not None and explicit.settings.vector_password is None


def test_overlay_cannot_change_backend_or_nonvector_config(tmp_path: Path) -> None:
    root = _root(tmp_path, "VECTOR_BACKEND=milvus\n", "APP_PORT=9000\nVECTOR_BACKEND=zilliz\n")
    loaded = load_env(root, environ={})
    assert loaded.settings is not None and loaded.settings.vector_backend == "milvus"
    assert loaded.settings.app_port == 8000
    assert {i.key for i in loaded.issues} == {"APP_PORT", "VECTOR_BACKEND"}


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("standalone:19530", "http://standalone:19530"),
        ("http://127.0.0.1:19530", "http://127.0.0.1:19530"),
        ("https://milvus.internal:19530/", "https://milvus.internal:19530"),
        ("[::1]:19530", "http://[::1]:19530"),
    ],
)
def test_milvus_network_uri(uri: str, expected: str) -> None:
    assert normalize_milvus_uri(uri) == expected


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "./local.db",
        "file:///tmp/vector.db",
        "localhost",
        "http://host:0",
        "http://host:65536",
        "http://host:bad",
        "http://host:19530/path",
        "http://host:19530?token=hidden",
        "http://root:hidden@host:19530",
        "http://bad host:19530",
    ],
)
def test_milvus_rejects_ambiguous_uri_without_echo(uri: str) -> None:
    with pytest.raises(ValueError) as error:
        normalize_milvus_uri(uri)
    assert str(error.value) in {"VECTOR_URI_REQUIRED", "VECTOR_URI_INVALID"}


@pytest.mark.parametrize("auth", ["password", "token", "none"])
def test_selected_credentials_never_fall_back_to_cloud(auth: str) -> None:
    values = {
        "vector_backend": "milvus",
        "vector_uri": "standalone:19530",
        "vector_database": "private_db",
        "vector_timeout_seconds": 7,
        "zilliz_cloud_token": "cloud-secret",
    }
    if auth == "password":
        values.update(vector_user="root", vector_password="fixture-pass")
    elif auth == "token":
        values["vector_token"] = "fixture-token"
    settings = EnvSettings.model_validate(values)
    factory = Mock(return_value=object())
    adapter = MilvusHybridAdapter(settings, client_factory=factory)
    adapter.connect()
    kwargs = factory.call_args.kwargs
    assert kwargs["db_name"] == "private_db" and kwargs["timeout"] == 7
    assert kwargs["uri"] == "http://standalone:19530"
    assert "cloud-secret" not in str(kwargs)
    assert ("user" in kwargs) == (auth == "password")
    assert ("token" in kwargs) == (auth == "token")
    if auth == "password":
        assert kwargs["password"] == "fixture-pass"
    assert "fixture-pass" not in str(adapter.safe_status())
    cloud = settings.model_copy(update={"vector_backend": "zilliz"})
    cloud_args = vector_connection_kwargs(cloud)
    assert cloud_args["token"] == "cloud-secret"
    assert "password" not in cloud_args and "user" not in cloud_args


@pytest.mark.parametrize(
    "values,code",
    [
        ({"VECTOR_USER": "root"}, "VECTOR_USER_PASSWORD_PAIR_REQUIRED"),
        ({"VECTOR_PASSWORD": "fixture-pass"}, "VECTOR_USER_PASSWORD_PAIR_REQUIRED"),
        (
            {
                "VECTOR_USER": "root",
                "VECTOR_PASSWORD": "fixture-pass",
                "VECTOR_TOKEN": "fixture-token",
            },
            "VECTOR_AUTH_METHODS_CONFLICT",
        ),
    ],
)
def test_incomplete_or_conflicting_auth_fails_before_connect(
    tmp_path: Path, values: dict, code: str
) -> None:
    root = _root(tmp_path, "VECTOR_BACKEND=milvus\nVECTOR_URI=standalone:19530\n")
    loaded = load_env(root, environ=values)
    assert loaded.settings is not None
    assert code in {issue.code for issue in conditional_issues(loaded)}
    factory = Mock()
    with pytest.raises(ValueError, match=code):
        MilvusHybridAdapter(loaded.settings, client_factory=factory).connect()
    factory.assert_not_called()


def test_milvus_gate_uses_own_dimension_and_bm25_keys(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        "VECTOR_BACKEND=milvus\nVECTOR_URI=standalone:19530\nVECTOR_DIMENSION=3\nEMBEDDING_DIMENSION=3\n",
    )
    good = load_env(root, environ={})
    assert not [i for i in conditional_issues(good) if i.key.startswith(("VECTOR_", "ZILLIZ_"))]
    bad = load_env(root, environ={"VECTOR_DIMENSION": "4", "VECTOR_ENABLE_BM25": "false"})
    assert {"VECTOR_DIMENSION_MISMATCH", "VECTOR_BM25_REQUIRED"}.issubset(
        {i.code for i in conditional_issues(bad)}
    )


def _settings() -> EnvSettings:
    return EnvSettings(
        vector_backend="milvus",
        vector_uri="standalone:19530",
        vector_database="private_db",
        vector_collection="private_chunks",
        vector_dimension=3,
        vector_dense_field="embedding",
        vector_sparse_field="keywords",
        vector_timeout_seconds=7,
    )


def test_schema_synthetic_writes_and_plan_share_local_fields() -> None:
    settings = _settings()
    plan = build_zilliz_collection_plan(settings)
    schema, _ = build_sdk_schema(MilvusClient, settings)
    fields = {field.name: field for field in schema.fields}
    assert fields["embedding"].params["dim"] == 3
    assert "keywords" in fields and "dense_vector" not in fields
    assert schema.functions[0].output_field_names == ["keywords"]
    assert plan["database_name_from"] == "VECTOR_DATABASE"
    records, _ = synthetic_records(settings)
    assert all(len(record["embedding"]) == 3 and "dense_vector" not in record for record in records)


class _ProvisionClient:
    create_schema = staticmethod(MilvusClient.create_schema)
    prepare_index_params = staticmethod(MilvusClient.prepare_index_params)

    def __init__(self) -> None:
        self.database = "default"
        self.events = []
        self.rows = {}
        self.indexes = []

    def _target(self, kwargs):
        assert kwargs["collection_name"] == "private_chunks"
        assert self.database == "private_db"
        if "timeout" in kwargs:
            assert kwargs["timeout"] == 7

    def list_databases(self, **kwargs):
        return ["default"]

    def create_database(self, **kwargs):
        self.events.append(("create_database", kwargs["db_name"]))

    def use_database(self, db_name):
        self.database = db_name

    def list_collections(self, **kwargs):
        return [f"existing_{n}" for n in range(6)]

    def has_collection(self, **kwargs):
        self._target(kwargs)
        return False

    def create_collection(self, **kwargs):
        self._target(kwargs)
        self.indexes = [
            i["index_name"] for i in build_zilliz_collection_plan(_settings())["schema"]["indexes"]
        ]
        self.events.append(("create_collection", kwargs["collection_name"]))

    def get_load_state(self, **kwargs):
        self._target(kwargs)
        return {"state": "Loaded"}

    def list_indexes(self, **kwargs):
        self._target(kwargs)
        return self.indexes

    def upsert(self, **kwargs):
        self._target(kwargs)
        for row in kwargs["data"]:
            assert len(row["embedding"]) == 3
            self.rows[row["zilliz_pk"]] = row
        return {"upsert_count": len(kwargs["data"])}

    def get(self, **kwargs):
        self._target(kwargs)
        return [self.rows[key] for key in kwargs["ids"] if key in self.rows]

    def search(self, **kwargs):
        self._target(kwargs)
        assert kwargs["anns_field"] in {"embedding", "keywords"}
        assert kwargs["consistency_level"] == "Strong"
        assert "ARRAY_CONTAINS_ANY" in kwargs["filter"]
        assert "permission_revision <= 12" in kwargs["filter"]
        row = next(row for key, row in self.rows.items() if key.endswith("_authorized"))
        return [[{"entity": row, "distance": 1}]]

    def delete(self, **kwargs):
        self._target(kwargs)
        for key in kwargs["ids"]:
            self.rows.pop(key)


def test_custom_milvus_database_provisions_from_default_and_does_not_use_cloud_limit() -> None:
    client = _ProvisionClient()
    factory = Mock(return_value=client)
    result = provision_and_validate(_settings(), approval=CREATE_APPROVAL, client_factory=factory)
    assert factory.call_args.kwargs["db_name"] == "default"
    assert client.events == [
        ("create_database", "private_db"),
        ("create_collection", "private_chunks"),
    ]
    assert result["database_name"] == "private_db"
    assert result["validation"]["cleanup_remaining_count"] == 0
    assert not client.rows


def test_loading_calls_actual_sdk_method() -> None:
    client = Mock(spec=MilvusClient)
    client.get_load_state.return_value = {"state": "NotLoad"}
    assert request_collection_load_if_needed(client, _settings()) == "load_requested"
    client.load_collection.assert_called_once_with(collection_name="private_chunks", timeout=7)
