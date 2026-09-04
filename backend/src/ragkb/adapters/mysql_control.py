"""MySQL control-plane connection adapter with secret-safe status."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any

from ragkb.config import EnvSettings
from ragkb.infrastructure.mysql_migrations import MYSQL_MIGRATION_REVISION, migration_plan


def _pymysql_connect(**kwargs: Any) -> Any:
    import pymysql  # type: ignore[import-untyped]

    return pymysql.connect(**kwargs)


class MySQLControlPlaneAdapter:
    revision = "mysql-control-plane:pymysql-pool:g4-v2"

    def __init__(
        self,
        settings: EnvSettings,
        *,
        connection_factory: Callable[..., Any] = _pymysql_connect,
    ) -> None:
        self._settings = settings
        self._connection_factory = connection_factory
        self._idle: queue.LifoQueue[Any] = queue.LifoQueue(maxsize=settings.mysql_pool_size)
        self._leases = threading.BoundedSemaphore(
            settings.mysql_pool_size + settings.mysql_max_overflow
        )
        self._lock = threading.Lock()
        self._created_connections = 0
        self._closed = False

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

    def _release(self, connection: Any) -> None:
        healthy = True
        try:
            connection.rollback()
        except Exception:
            healthy = False
        with self._lock:
            closed = self._closed
        if closed or not healthy:
            try:
                connection.close()
            finally:
                with self._lock:
                    self._created_connections -= 1
        else:
            try:
                self._idle.put_nowait(connection)
            except queue.Full:
                try:
                    connection.close()
                finally:
                    with self._lock:
                        self._created_connections -= 1
        self._leases.release()

    def connect(self) -> Any:
        if not self._leases.acquire(timeout=self._settings.mysql_connect_timeout_seconds):
            raise TimeoutError("MYSQL_CONNECTION_POOL_EXHAUSTED")
        try:
            with self._lock:
                if self._closed:
                    raise RuntimeError("MYSQL_CONNECTION_POOL_CLOSED")
            try:
                connection = self._idle.get_nowait()
                ping = getattr(connection, "ping", None)
                if callable(ping):
                    ping(reconnect=True)
            except queue.Empty:
                connection = self._connect(include_database=True)
                with self._lock:
                    self._created_connections += 1
            except Exception:
                try:
                    connection.close()
                finally:
                    with self._lock:
                        self._created_connections -= 1
                connection = self._connect(include_database=True)
                with self._lock:
                    self._created_connections += 1
            return _PooledConnection(connection, self._release)
        except Exception:
            self._leases.release()
            raise

    def connect_server(self) -> Any:
        return self._connect(include_database=False)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        while True:
            try:
                connection = self._idle.get_nowait()
            except queue.Empty:
                break
            try:
                connection.close()
            finally:
                with self._lock:
                    self._created_connections -= 1

    def safe_status(self) -> dict[str, object]:
        return {
            "adapter": self.revision,
            "migration_revision": MYSQL_MIGRATION_REVISION,
            "migration_count": migration_plan()["statement_count"],
            "host_configured": bool(self._settings.mysql_host),
            "database_configured": bool(self._settings.mysql_database),
            "ssl_enabled": self._settings.mysql_ssl,
            "pool_size": self._settings.mysql_pool_size,
            "max_overflow": self._settings.mysql_max_overflow,
            "created_connections": self._created_connections,
            "password_in_status": False,
            "real_connection_attempted": False,
        }


class _PooledConnection:
    """Connection facade whose close returns the physical connection to the pool."""

    def __init__(self, connection: Any, release: Callable[[Any], None]) -> None:
        self._connection = connection
        self._release = release
        self._returned = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def close(self) -> None:
        if self._returned:
            return
        self._returned = True
        self._release(self._connection)

    def __enter__(self) -> _PooledConnection:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
