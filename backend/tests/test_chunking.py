from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from ragkb.document_processing.chunking import (
    ChunkingConfig,
    SemanticChunker,
    TokenAwareChunker,
    TokenizerArtifact,
    count_tokens,
)
from ragkb.domain.documents import CanonicalDocument, CanonicalNode, NodeType, SourceLocator


def _document(text: str) -> CanonicalDocument:
    heading = CanonicalNode(
        "heading-1", None, NodeType.HEADING, "出差制度", "出差制度", SourceLocator(page=1)
    )
    paragraph = CanonicalNode(
        "paragraph-1", None, NodeType.PARAGRAPH, text, text, SourceLocator(page=1)
    )
    return CanonicalDocument(
        document_version_id="version-1",
        language="zh",
        source_format="txt",
        nodes=(heading, paragraph),
        parser_revision="test",
        normalization_revision="test",
        content_checksum=hashlib.sha256(text.encode()).hexdigest(),
    )


def test_token_chunker_preserves_overlap_structure_and_parent_context() -> None:
    text = "一线城市住宿标准每晚六百元。" * 12
    result = TokenAwareChunker(
        ChunkingConfig(
            target_tokens=24, overlap_tokens=6, min_tokens=4, max_tokens=24, parent_max_tokens=48
        )
    ).chunk(_document(text), tenant_id="tenant-1")

    assert len(result.chunks) > 1
    assert all(item.token_count <= 24 for item in result.chunks)
    assert all(item.parent_chunk_id for item in result.chunks)
    assert all(item.metadata["heading"] == "出差制度" for item in result.chunks)
    assert all(parent.token_count <= 48 for parent in result.parent_chunks)
    assert count_tokens("南京市长江大桥") == 7


def test_chunk_ids_are_stable_for_reindexing() -> None:
    chunker = TokenAwareChunker(
        ChunkingConfig(target_tokens=20, overlap_tokens=4, min_tokens=2, max_tokens=20)
    )
    first = chunker.chunk(_document("保修期为三年。" * 5), tenant_id="tenant-1")
    second = chunker.chunk(_document("保修期为三年。" * 5), tenant_id="tenant-1")

    assert [item.id for item in first.chunks] == [item.id for item in second.chunks]


def test_semantic_chunker_merges_related_nodes_and_splits_on_low_similarity() -> None:
    document = _document("policy alpha")
    extra = (
        CanonicalNode(
            "paragraph-2",
            None,
            NodeType.PARAGRAPH,
            "policy beta",
            "policy beta",
            SourceLocator(page=1),
        ),
        CanonicalNode(
            "paragraph-3",
            None,
            NodeType.PARAGRAPH,
            "unrelated recipe",
            "unrelated recipe",
            SourceLocator(page=2),
        ),
    )
    document = CanonicalDocument(**{**document.__dict__, "nodes": (*document.nodes, *extra)})
    chunker = SemanticChunker(
        lambda left, right: 1.0 if left.split()[0] == right.split()[0] else 0.0,
        threshold=0.5,
        config=ChunkingConfig(
            strategy="semantic",
            target_tokens=20,
            overlap_tokens=2,
            min_tokens=1,
            max_tokens=20,
        ),
    )

    result = chunker.chunk(document, tenant_id="tenant")

    assert any("policy alpha\npolicy beta" in item.display_text for item in result.chunks)
    assert any(item.display_text == "unrelated recipe" for item in result.chunks)


def test_pinned_tokenizer_artifact_rejects_hash_drift_and_drives_counts() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "backend/tests/fixtures/tokenizer/minimal-tokenizer.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    tokenizer = TokenizerArtifact(path, digest, "test-wordlevel-v1")

    assert count_tokens("test token", tokenizer) == 2
    with pytest.raises(ValueError, match="SHA256_MISMATCH"):
        TokenizerArtifact(path, "0" * 64, "test-wordlevel-v1")


def test_table_chunks_repeat_the_reviewed_header_context() -> None:
    node = CanonicalNode(
        "table-row-2",
        None,
        NodeType.TABLE,
        "北京 | 600 元",
        "北京 | 600 元",
        SourceLocator(sheet="policy", row=2),
        metadata={"table_header": "地区 | 住宿上限"},
    )
    document = CanonicalDocument(
        document_version_id="table-version",
        language="zh",
        source_format="xlsx",
        nodes=(node,),
        parser_revision="test-table:v1",
        normalization_revision="test-normalization:v1",
        content_checksum="a" * 64,
    )

    result = TokenAwareChunker(
        ChunkingConfig(target_tokens=20, overlap_tokens=2, min_tokens=1, max_tokens=20)
    ).chunk(document, tenant_id="tenant")

    assert result.chunks
    assert "TABLE_HEADER: 地区 | 住宿上限" in result.chunks[0].retrieval_text
