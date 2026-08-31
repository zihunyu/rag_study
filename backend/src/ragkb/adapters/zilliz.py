"""Zilliz Cloud China connection contract backed by pymilvus.MilvusClient."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pymilvus import MilvusClient

from ragkb.config import EnvSettings


class ZillizCloudAdapter:
    revision = "zilliz-cloud-pymilvus:v1"

    def __init__(
        self,
        settings: EnvSettings,
        *,
        client_factory: Callable[..., Any] = MilvusClient,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory

    def connect(self) -> Any:
        token = self._settings.zilliz_cloud_token
        return self._client_factory(
            uri=self._settings.zilliz_cloud_uri,
            token=token.get_secret_value() if token is not None else "",
            db_name=self._settings.zilliz_cloud_database,
            timeout=self._settings.zilliz_cloud_timeout_seconds,
        )

    def safe_status(self) -> dict[str, object]:
        return {
            "adapter": self.revision,
            "database_configured": bool(self._settings.zilliz_cloud_database),
            "collection_configured": bool(self._settings.zilliz_cloud_collection),
            "bm25_enabled": self._settings.zilliz_cloud_enable_bm25,
            "security_consistency": self._settings.zilliz_cloud_security_consistency_level,
            "token_in_status": False,
            "real_connection_attempted": False,
        }
