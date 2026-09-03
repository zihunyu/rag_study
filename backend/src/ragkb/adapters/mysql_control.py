"""MySQL control-plane connection adapter with secret-safe status."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ragkb.config import EnvSettings
from ragkb.infrastructure.mysql_migrations import MYSQL_MIGRATION_REVISION, migration_plan


def _pymysql_connect(**kwargs: Any) -> Any:
    import pymysql  # type: ignore[import-untyped]

    return pymysql.connect(**kwargs)


class MySQLControlPlaneAdapter:
    revision = "mysql-control-plane:pymysql:g2-v1"

    def __init__(
        self,
        settings: EnvSettings,
        *,
        connection_factory: Callable[..., Any] = _pymysql_connect,
    ) -> None:
        self._settings = settings
        self._connection_factory = connection_factory

    def _connect(self, *, include_database: bool) -> Any:
        password = self._settings.mysql_password
        ssl = {"check_hostname": True} if self._settings.mysql_ssl else None
        parameters: dict[str, object] = {
            "host": self._settings.mysql_host,
            "port": self._settings.mysql_port,
            "user": self._settings.mysql_user,
            "password": password.get_secret_value() if password is not None else "",
            "charset": self._settings.mysql_charset,
            "connect_timeout": self._settings.mysql_connect_timeout_seconds,
            "autocommit": False,
            "ssl": ssl,
        }
        if include_database:
            parameters["database"] = self._settings.mysql_database
        return self._connection_factory(**parameters)

    def connect(self) -> Any:
        return self._connect(include_database=True)

    def connect_server(self) -> Any:
        return self._connect(include_database=False)

    def safe_status(self) -> dict[str, object]:
        return {
            "adapter": self.revision,
            "migration_revision": MYSQL_MIGRATION_REVISION,
            "migration_count": migration_plan()["statement_count"],
            "host_configured": bool(self._settings.mysql_host),
            "database_configured": bool(self._settings.mysql_database),
            "ssl_enabled": self._settings.mysql_ssl,
            "password_in_status": False,
            "real_connection_attempted": False,
        }
