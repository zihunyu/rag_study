"""Cross-process authorization linearization without holding model calls or RAG writes."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

from filelock import FileLock

from ragkb.infrastructure.workspace_db import WorkspaceDB


@contextmanager
def permission_guard(db: WorkspaceDB, tenant: str) -> Iterator[None]:
    name = "ragkb-auth-" + hashlib.sha256(tenant.encode()).hexdigest()[:40]
    if db.mysql:
        with db.connection() as connection:
            row = db.one(connection, "SELECT GET_LOCK(?, 30) AS acquired", (name,))
            if not row or row["acquired"] != 1:
                raise TimeoutError("AUTHORIZATION_LOCK_TIMEOUT")
            try:
                yield
            finally:
                db.execute(connection, "SELECT RELEASE_LOCK(?)", (name,))
    else:
        # Separate file lock allows the QA repository to persist through another SQLite
        # connection while access mutations remain serialized across processes.
        with FileLock(str(db.database.path) + "." + name + ".lock", timeout=30):
            yield
