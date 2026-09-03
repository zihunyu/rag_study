from __future__ import annotations

from pathlib import Path

from ragkb.adapters.sqlite_retrieval import SQLiteRetrievalControlPlane
from ragkb.domain.retrieval import AuthorizedChunk, SearchContext
from ragkb.infrastructure.sqlite import SQLiteDatabase


def _chunk(chunk_id: str, visibility: str, acl: tuple[str, ...]) -> AuthorizedChunk:
    return AuthorizedChunk(
        chunk_id=chunk_id,
        tenant_id="tenant-1",
        space_id="space-1",
        document_id=f"document-{chunk_id}",
        document_version_id=f"version-{chunk_id}",
        parent_chunk_id=None,
        display_text="evidence",
        retrieval_text="evidence",
        locator={"page": 1},
        content_checksum=f"checksum-{chunk_id}",
        visibility="RESTRICTED" if visibility == "RESTRICTED" else "TENANT",
        acl_scope_tokens=acl,
        classification_level=1,
        lifecycle_projection="SERVING",
        valid_from_epoch=0,
        valid_to_epoch=0,
        permission_revision=2,
        current_version=True,
    )


def test_sqlite_retrieval_adapter_retains_acl_and_tenant_fail_closed_semantics(
    tmp_path: Path,
) -> None:
    adapter = SQLiteRetrievalControlPlane(SQLiteDatabase(tmp_path / "control.sqlite3"))
    allowed = _chunk("allowed", "RESTRICTED", ("group:reader",))
    denied = _chunk("denied", "RESTRICTED", ("group:secret",))
    adapter.put_for_test(allowed)
    adapter.put_for_test(denied)
    context = SearchContext(
        tenant_id="tenant-1",
        space_ids=("space-1",),
        subject_scope_tokens=("group:reader",),
        clearance_level=2,
        as_of_epoch=100,
        active_generation_id="generation-1",
        active_permission_revision=2,
        required_security_watermark=0,
    )

    result = adapter.authorize_chunks(("allowed", "denied"), context)

    assert set(result) == {"allowed"}
    assert adapter.authorize_parent("allowed", context) == allowed
