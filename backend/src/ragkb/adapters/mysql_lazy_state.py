"""Load addressed rows on demand; unrelated tenant history never enters a write UoW."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, ItemsView, Iterator
from copy import deepcopy
from typing import Any

from ragkb.adapters.mysql_entity_store import EntityMap


class LazyCollection(dict[str, Any]):
    def __init__(self, kind: str, loader: Callable[..., EntityMap]) -> None:
        super().__init__()
        self.kind, self.loader = kind, loader
        self._loaded: set[str] = set()
        self._all_loaded = False

    def _load(self, key: str | None = None) -> None:
        if self._all_loaded or (key is not None and key in self._loaded):
            return
        filters: dict[str, Any] = {"entity_type": self.kind}
        grouped = self.kind in {"reviews", "lineage"}
        if key is not None:
            identity = (
                hashlib.sha256(f"idempotency:{key}".encode()).hexdigest()
                if self.kind == "idempotency"
                else key
            )
            filters["parent_id" if grouped else "entity_id"] = identity
        rows = self.loader(**filters)
        grouped_rows: dict[str, list[Any]] = {}
        for row in rows.values():
            if grouped and row.parent_id is not None:
                grouped_rows.setdefault(row.parent_id, []).append(deepcopy(row.payload))
            elif row.logical_key not in self._loaded:
                super().__setitem__(row.logical_key, deepcopy(row.payload))
        for parent, values in grouped_rows.items():
            if parent not in self._loaded:
                super().__setitem__(parent, values)
        if key is None:
            self._all_loaded = True
        else:
            self._loaded.add(key)

    def __getitem__(self, key: str) -> Any:
        self._load(key)
        return super().__getitem__(key)

    def __iter__(self) -> Iterator[str]:
        self._load()
        return super().__iter__()

    def __contains__(self, key: object) -> bool:
        self._load(str(key))
        return super().__contains__(key)

    def get(self, key: str, default: Any = None) -> Any:
        self._load(key)
        return super().get(key, default)

    def __setitem__(self, key: str, value: Any) -> None:
        self._load(key)
        super().__setitem__(key, value)
        self._loaded.add(key)

    def setdefault(self, key: str, default: Any = None) -> Any:
        self._load(key)
        return super().setdefault(key, default)

    def items(self) -> Any:
        self._load()
        return super().items()

    def values(self) -> Any:
        self._load()
        return super().values()

    def loaded_items(self) -> ItemsView[str, Any]:
        return super().items()


def loaded_items(value: dict[str, Any]) -> ItemsView[str, Any]:
    return value.loaded_items() if isinstance(value, LazyCollection) else value.items()


class LazyAggregateState(dict[str, Any]):
    """Legacy aggregate APIs load only collections they actually address."""

    def __init__(self, empty: Callable[[], dict[str, Any]], loader: Callable[[str], Any]) -> None:
        super().__init__()
        self.empty, self.loader = empty, loader

    def __getitem__(self, key: str) -> Any:
        if key not in self:
            super().__setitem__(key, self.loader(key))
        return super().__getitem__(key)

    def snapshot(self) -> dict[str, Any]:
        return {**self.empty(), **self}
