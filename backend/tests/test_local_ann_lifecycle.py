from __future__ import annotations

import hashlib

import pytest
from ragkb.adapters.local_indexing import SQLiteLocalHybridIndex, SQLiteLocalIndexingSink
from ragkb.adapters.sqlite_retrieval import SQLiteRetrievalControlPlane
from ragkb.adapters.stubs import DeterministicEmbedding
from ragkb.document_processing.chunking import ChunkingConfig, TokenAwareChunker
from ragkb.domain.documents import CanonicalDocument, CanonicalNode, NodeType, SourceLocator
from ragkb.domain.errors import IngestionCancelled
from ragkb.infrastructure.sqlite import SQLiteDatabase


def _chunks(version_id: str, text: str):
    document = CanonicalDocument(
        version_id,
        "zh",
        "txt",
        (CanonicalNode("node", None, NodeType.PARAGRAPH, text, text, SourceLocator(page=1)),),
        "parser",
        "normalizer",
        hashlib.sha256(text.encode()).hexdigest(),
    )
    return TokenAwareChunker(
        ChunkingConfig(target_tokens=20, overlap_tokens=2, min_tokens=1, max_tokens=20)
    ).chunk(document, tenant_id="tenant")


def test_ann_manifest_revision_lru_and_retirement_cleanup(tmp_path) -> None:
    database = SQLiteDatabase(tmp_path / "index.sqlite3")
    control = SQLiteRetrievalControlPlane(database)
    sink = SQLiteLocalIndexingSink(
        database,
        control,
        DeterministicEmbedding(),
        generation_id="generation-1",
    )
    index = SQLiteLocalHybridIndex(database, max_cached_generations=1, max_disk_snapshots=1)

    sink.index(
        _chunks("version-1", "第一版保修期为三年"),
        document_id="document-1",
        tenant_id="tenant",
        space_id="space",
    )
    first = index._snapshot("generation-1")
    sink.index(
        _chunks("version-1", "第二版保修期为五年"),
        document_id="document-1",
        tenant_id="tenant",
        space_id="space",
    )
    second = index._snapshot("generation-1")

    assert first.signature != second.signature
    assert first.path != second.path

    second_sink = SQLiteLocalIndexingSink(
        database,
        control,
        DeterministicEmbedding(),
        generation_id="generation-2",
    )
    second_sink.index(
        _chunks("version-2", "另一代索引"),
        document_id="document-2",
        tenant_id="tenant",
        space_id="space",
    )
    current = index._snapshot("generation-2")
    assert list(index._snapshots) == ["generation-2"]

    index.retire_generation("generation-2")

    assert "generation-2" not in index._snapshots
    assert current.path is not None and not current.path.exists()
    with database.connect() as connection:
        row = connection.execute(
            "SELECT retired FROM local_index_generations WHERE generation_id = ?",
            ("generation-2",),
        ).fetchone()
    assert int(row["retired"]) == 1
    with pytest.raises(KeyError, match="GENERATION_RETIRED"):
        index._snapshot("generation-2")


def test_local_index_cancellation_after_write_removes_partial_projection(tmp_path) -> None:
    database = SQLiteDatabase(tmp_path / "cancel.sqlite3")
    control = SQLiteRetrievalControlPlane(database)
    sink = SQLiteLocalIndexingSink(
        database,
        control,
        DeterministicEmbedding(),
        generation_id="cancel-generation",
    )
    checks = iter((False, True))

    with pytest.raises(IngestionCancelled):
        sink.index(
            _chunks("cancel-version", "取消中的索引"),
            document_id="cancel-document",
            tenant_id="tenant",
            space_id="space",
            cancel_check=lambda: next(checks),
        )

    with database.connect() as connection:
        index_count = connection.execute(
            "SELECT COUNT(*) AS count FROM local_search_index"
        ).fetchone()
        projection_count = connection.execute(
            "SELECT COUNT(*) AS count FROM retrieval_projections"
        ).fetchone()
    assert int(index_count["count"]) == 0
    assert int(projection_count["count"]) == 0
