from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from ragkb.adapters.local_storage import (
    LocalFileStorage,
    StoragePathError,
)
from ragkb.contracts.ports import StorageIntegrityError


@pytest.mark.parametrize(
    "key",
    ["../escape.bin", "nested/../../escape.bin", "/absolute.bin", "C:\\absolute.bin"],
)
def test_storage_rejects_path_escape(tmp_path: Path, key: str) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    storage.ensure_layout()

    with pytest.raises(StoragePathError):
        storage.write_bytes("original", key, b"blocked")


def test_storage_writes_atomically_inside_partition(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    storage.ensure_layout()

    target = storage.write_bytes("artifacts", "tenant/document/result.json", b'{"ok":true}')

    assert target.is_file()
    assert storage.read_bytes("artifacts", "tenant/document/result.json") == b'{"ok":true}'
    assert not list(target.parent.glob("*.tmp"))


def test_storage_rejects_unknown_partition(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "storage")

    with pytest.raises(StoragePathError):
        storage.write_bytes("unknown", "file.bin", b"blocked")


def test_promote_is_recoverable_without_overwriting_an_immutable_original(
    tmp_path: Path,
) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    storage.ensure_layout()
    storage.write_bytes("quarantine", "session/source.txt", b"first")

    expected = hashlib.sha256(b"first").hexdigest()
    promoted = storage.promote(
        "quarantine", "session/source.txt", "document/original.txt", expected
    )

    assert promoted.read_bytes() == b"first"
    assert (
        storage.promote("quarantine", "session/source.txt", "document/original.txt", expected)
        == promoted
    )
    storage.write_bytes("quarantine", "session/source.txt", b"replacement")
    with pytest.raises(FileExistsError):
        storage.promote("quarantine", "session/source.txt", "document/original.txt", expected)
    assert promoted.read_bytes() == b"first"

    storage.delete("quarantine", "session/source.txt")
    promoted.write_bytes(b"corrupted")
    with pytest.raises(StorageIntegrityError, match="DOC_ORIGINAL_HASH_MISMATCH"):
        storage.promote("quarantine", "session/source.txt", "document/original.txt", expected)
