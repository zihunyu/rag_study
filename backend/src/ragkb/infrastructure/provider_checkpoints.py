"""Atomic JSON checkpoints containing no provider credentials or source content."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class JsonCheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def _load(self) -> dict[str, dict[str, dict[str, Any]]]:
        if not self.path.is_file():
            return {}
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("CHECKPOINT_INVALID")
        return loaded

    def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        item = self._load().get(namespace, {}).get(key)
        return dict(item) if item is not None else None

    def save(self, namespace: str, key: str, value: Mapping[str, Any]) -> None:
        payload = self._load()
        payload.setdefault(namespace, {})[key] = dict(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".provider-checkpoint-", suffix=".json", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
