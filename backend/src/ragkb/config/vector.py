"""Resolve the selected vector service consistently for reads, writes and provisioning."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ragkb.config.env import EnvSettings


def normalize_milvus_uri(value: str) -> str:
    uri = value.strip()
    if not uri:
        raise ValueError("VECTOR_URI_REQUIRED")
    if "://" not in uri:
        uri = "http://" + uri
    try:
        parsed = urlsplit(uri)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.port is not None
            and parsed.port > 0
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and not any(char.isspace() for char in uri)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("VECTOR_URI_INVALID")
    return uri.rstrip("/")


def vector_database(settings: EnvSettings) -> str:
    return (
        settings.vector_database
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_database
    )


def vector_collection_name(settings: EnvSettings) -> str:
    return (
        settings.vector_collection
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_collection
    )


def vector_dense_field(settings: EnvSettings) -> str:
    return (
        settings.vector_dense_field
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_dense_field
    )


def vector_sparse_field(settings: EnvSettings) -> str:
    return (
        settings.vector_sparse_field
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_sparse_field
    )


def vector_metric_type(settings: EnvSettings) -> str:
    return (
        settings.vector_metric_type
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_metric_type
    )


def vector_dimension(settings: EnvSettings) -> int:
    return (
        settings.vector_dimension
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_dimension
    )


def vector_analyzer(settings: EnvSettings) -> str:
    return (
        settings.vector_bm25_analyzer
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_bm25_analyzer
    )


def vector_timeout(settings: EnvSettings) -> float:
    return (
        settings.vector_timeout_seconds
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_timeout_seconds
    )


def vector_consistency(settings: EnvSettings) -> str:
    return (
        settings.vector_consistency_level
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_consistency_level
    )


def vector_security_consistency(settings: EnvSettings) -> str:
    return (
        settings.vector_security_consistency_level
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_security_consistency_level
    )


def vector_connection_kwargs(
    settings: EnvSettings, *, database: str | None = None
) -> dict[str, Any]:
    """Only pass this dictionary to the SDK; it contains credentials."""
    kwargs: dict[str, Any] = {
        "db_name": vector_database(settings) if database is None else database,
        "timeout": vector_timeout(settings),
    }
    if settings.vector_backend == "milvus":
        kwargs["uri"] = normalize_milvus_uri(settings.vector_uri)
        token = settings.vector_token
        password = settings.vector_password
        if token is not None and (settings.vector_user or password is not None):
            raise ValueError("VECTOR_AUTH_METHODS_CONFLICT")
        if bool(settings.vector_user) != (password is not None):
            raise ValueError("VECTOR_USER_PASSWORD_PAIR_REQUIRED")
        if token is not None:
            kwargs["token"] = token.get_secret_value()
        elif settings.vector_user and password is not None:
            kwargs.update(user=settings.vector_user, password=password.get_secret_value())
    else:
        kwargs["uri"] = settings.zilliz_cloud_uri
        token = settings.zilliz_cloud_token
        kwargs["token"] = token.get_secret_value() if token is not None else ""
    return kwargs
