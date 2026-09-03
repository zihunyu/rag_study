from __future__ import annotations

import hashlib
import io
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.runtime_components import build_runtime_components


def _wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8_000)
        audio.writeframes(b"\x00\x00" * 80)
    return buffer.getvalue()


def _complete(client: TestClient, space_id: str, filename: str, content: bytes, mime: str):
    key = filename.replace(".", "-")
    created = client.post(
        f"/api/v1/spaces/{space_id}/upload-sessions",
        headers={"Idempotency-Key": f"binary-create-{key}"},
        json={
            "filename": filename,
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "declared_mime": mime,
        },
    )
    uploaded = client.put(
        created.json()["upload_path"],
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    return client.post(
        f"/api/v1/upload-sessions/{created.json()['upload_session_id']}:complete",
        headers={
            "If-Match": uploaded.headers["etag"],
            "Idempotency-Key": f"binary-complete-{key}",
        },
    )


@pytest.mark.parametrize(
    ("filename", "content", "mime"),
    [
        ("valid.wav", _wav(), "audio/wav"),
        ("valid.png", b"\x89PNG\r\n\x1a\nsynthetic", "image/png"),
        ("valid.jpg", b"\xff\xd8\xffsynthetic", "image/jpeg"),
        ("valid.gif", b"GIF89asynthetic", "image/gif"),
        ("valid.tiff", b"II*\x00synthetic", "image/tiff"),
        ("valid-id3.mp3", b"ID3synthetic", "audio/mpeg"),
        ("valid-frame.mp3", b"\xff\xfbsynthetic", "audio/mpeg"),
        ("valid.m4a", b"\x00\x00\x00\x18ftypM4Asynthetic", "audio/mp4"),
    ],
)
def test_binary_magic_success_never_falls_through_to_utf8(
    tmp_path: Path, filename: str, content: bytes, mime: str
) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    response = _complete(
        TestClient(create_app(components)), components.space_id, filename, content, mime
    )

    assert response.status_code == 202


@pytest.mark.parametrize(
    ("filename", "content", "mime"),
    [
        ("bad.wav", b"RIFFshort", "audio/wav"),
        ("bad.png", b"PNG", "image/png"),
        ("bad.jpg", b"\xff\xd8", "image/jpeg"),
        ("bad.gif", b"GIF00a", "image/gif"),
        ("bad.tiff", b"II00", "image/tiff"),
        ("bad.mp3", b"not-mp3", "audio/mpeg"),
        ("bad.m4a", b"ftyp-at-wrong-offset", "audio/mp4"),
    ],
)
def test_truncated_or_fake_binary_magic_is_rejected(
    tmp_path: Path, filename: str, content: bytes, mime: str
) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    response = _complete(
        TestClient(create_app(components)), components.space_id, filename, content, mime
    )

    assert response.status_code == 422
    assert response.json()["code"] == "DOC_MAGIC_MISMATCH"
