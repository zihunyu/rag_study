"""Zilliz/Milvus schema construction and database selection policy."""

from __future__ import annotations

from typing import Any

from pymilvus import DataType, Function, FunctionType

from ragkb.config import EnvSettings
from ragkb.infrastructure.zilliz_plan import build_zilliz_collection_plan


class ZillizSchemaConflict(RuntimeError):
    pass


class ZillizCollectionCapacityError(RuntimeError):
    pass


def database_creation_required(settings: EnvSettings, listed_databases: set[str]) -> bool:
    return (
        settings.zilliz_cloud_database.casefold() != "default"
        and settings.zilliz_cloud_database not in listed_databases
    )


def database_switch_required(settings: EnvSettings) -> bool:
    return settings.zilliz_cloud_database.casefold() != "default"


def _datatype(name: str) -> DataType:
    return {
        "BOOL": DataType.BOOL,
        "VARCHAR": DataType.VARCHAR,
        "ARRAY": DataType.ARRAY,
        "INT8": DataType.INT8,
        "INT32": DataType.INT32,
        "INT64": DataType.INT64,
        "FLOAT_VECTOR": DataType.FLOAT_VECTOR,
        "SPARSE_FLOAT_VECTOR": DataType.SPARSE_FLOAT_VECTOR,
    }[name]


def build_sdk_schema(client: Any, settings: EnvSettings) -> tuple[Any, Any]:
    plan = build_zilliz_collection_plan(settings)
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    for field in plan["schema"]["fields"]:
        field_name = str(field["name"])
        field_type = str(field["type"])
        kwargs = {
            key: value
            for key, value in field.items()
            if key not in {"name", "type", "primary", "dimension"}
        }
        if field.get("primary"):
            kwargs["is_primary"] = True
            kwargs["auto_id"] = False
        if field_type == "FLOAT_VECTOR":
            kwargs["dim"] = int(field["dimension"])
        if field_type == "ARRAY":
            kwargs["element_type"] = _datatype(str(field["element_type"]))
        schema.add_field(field_name=field_name, datatype=_datatype(field_type), **kwargs)
    schema.add_function(
        Function(
            name="retrieval_text_bm25",
            function_type=FunctionType.BM25,
            input_field_names=["retrieval_text"],
            output_field_names=[settings.zilliz_cloud_sparse_field],
        )
    )
    index_params = client.prepare_index_params()
    for index in plan["schema"]["indexes"]:
        kwargs = {
            key: value
            for key, value in index.items()
            if key not in {"field", "index_type", "index_name"}
        }
        index_params.add_index(
            field_name=str(index["field"]),
            index_type=str(index["index_type"]),
            index_name=str(index["index_name"]),
            **kwargs,
        )
    return schema, index_params
