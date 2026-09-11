from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from ragkb.config import build_env_report, load_env
from ragkb.config.env import EnvSettings
from ragkb.document_processing.chunking import (
    ChunkingConfig,
    TokenAwareChunker,
    TokenizerArtifact,
    _windows,
)
from ragkb.domain.documents import CanonicalDocument, CanonicalNode, NodeType, SourceLocator
from ragkb.runtime_profiles.production import ProductionRuntimeFactory
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "backend/tests/fixtures/tokenizer/minimal-tokenizer.json"
OFFICIAL = ROOT / "backend/resources/tokenizers/qwen3-embedding-0.6b/tokenizer.json"


def _settings(path: Path) -> EnvSettings:
    return EnvSettings(
        app_env="production",
        rag_runtime_profile="production",
        tokenizer_artifact_path=path,
        tokenizer_artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        tokenizer_id="renamed-provider-tokenizer-v1",
    )


def test_production_factory_rejects_chinese_collapsing_tokenizer_even_when_renamed(tmp_path):
    path = tmp_path / "provider.json"
    path.write_bytes(FIXTURE.read_bytes())
    settings = _settings(path)
    old = TokenizerArtifact(path, settings.tokenizer_artifact_sha256, settings.tokenizer_id)
    assert len(old.spans("设备在常温条件下正常运行并应遵守维护保养规定" * 100)) == 1

    with pytest.raises(ValueError, match="TOKENIZER_PRODUCTION_UNSUITABLE"):
        ProductionRuntimeFactory().build_tokenizer(settings, tmp_path)


@pytest.mark.parametrize("invalid_json", [False, True])
def test_production_gate_checks_tokenizer_content_not_only_its_digest(tmp_path, invalid_json):
    path = tmp_path / "provider.json"
    path.write_bytes(b"{}" if invalid_json else FIXTURE.read_bytes())
    settings = _settings(path)
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            f"{key}={value}"
            for key, value in {
                "APP_ENV": "production",
                "TOKENIZER_ARTIFACT_PATH": str(path),
                "TOKENIZER_ARTIFACT_SHA256": settings.tokenizer_artifact_sha256,
                "TOKENIZER_ID": settings.tokenizer_id,
            }.items()
        ),
        encoding="utf-8",
    )
    report = build_env_report(load_env(ROOT, env_path=env, environ={}), "G4")
    code = "TOKENIZER_ARTIFACT_INVALID" if invalid_json else "TOKENIZER_PRODUCTION_UNSUITABLE"
    assert f"TOKENIZER_ARTIFACT_PATH:{code}" in report["gate_blockers"]
    assert "设备" not in json.dumps(report, ensure_ascii=False)


def test_saved_truncation_does_not_silently_drop_the_end_of_a_document(tmp_path):
    words = "alpha beta gamma delta epsilon zeta".split()
    native = Tokenizer(
        WordLevel(
            {"[UNK]": 0, **{v: i + 1 for i, v in enumerate(words)}},
            unk_token="[UNK]",  # noqa: S106 - tokenizer vocabulary marker, not a credential
        )
    )
    native.pre_tokenizer = Whitespace()
    native.enable_truncation(max_length=3)
    native.enable_padding(length=8, pad_id=0, pad_token="[UNK]")  # noqa: S106
    path = tmp_path / "truncating.json"
    native.save(str(path))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    tokenizer = TokenizerArtifact(path, digest, "truncation-regression")
    text = " ".join(words)

    assert tokenizer.spans(text) == tuple(
        (text.index(word), text.index(word) + len(word)) for word in words
    )


@pytest.fixture(scope="module")
def official_tokenizer():
    provenance = json.loads(OFFICIAL.with_name("provenance.json").read_text(encoding="utf-8"))
    settings = _settings(OFFICIAL).model_copy(
        update={
            "tokenizer_artifact_sha256": provenance["sha256"],
            "tokenizer_id": "Qwen/Qwen3-Embedding-0.6B@" + provenance["revision"],
        }
    )
    return ProductionRuntimeFactory().build_tokenizer(settings, ROOT)


def test_official_tokenizer_bounds_the_original_2200_character_regression(official_tokenizer):
    text = "设备在常温条件下正常运行并应遵守维护保养规定" * 100
    native = Tokenizer.from_file(str(OFFICIAL))
    assert len(official_tokenizer.spans(text)) == len(
        native.encode(text, add_special_tokens=False).ids
    )
    assert len(official_tokenizer.spans(text)) > 600
    windows = _windows(text, ChunkingConfig(), official_tokenizer)
    assert len(windows) > 1
    assert windows[0][1] == 0 and windows[-1][2] == len(text)
    assert all(
        len(native.encode(piece, add_special_tokens=False).ids) <= 800 for piece, _, _ in windows
    )


@pytest.mark.parametrize(
    "text",
    [
        "😀🧪𠮷野家繁體中文한국어日本語" * 30,
        "https://example.invalid/AX-2026?mode=offline warranty_condition_not_supported " * 20,
        "甲设备|480 W|960 Wh\n乙设备|960 W|1920 Wh\n" * 30,
    ],
    ids=["multilingual", "identifiers", "table"],
)
def test_bpe_windows_preserve_source_offsets_and_reencoded_budget(official_tokenizer, text):
    config = ChunkingConfig(target_tokens=11, overlap_tokens=3, min_tokens=1, max_tokens=11)
    native = Tokenizer.from_file(str(OFFICIAL))
    covered = set()
    for piece, start, end in _windows(text, config, official_tokenizer):
        assert piece == text[start:end].strip()
        assert len(native.encode(piece, add_special_tokens=False).ids) <= config.max_tokens
        covered.update(range(start, end))
    assert all(i in covered for i, char in enumerate(text) if not char.isspace())


def test_parent_budget_includes_real_bpe_separator_tokens(official_tokenizer):
    text = "alpha beta"
    document = CanonicalDocument(
        document_version_id="parent-regression",
        language="en",
        source_format="txt",
        nodes=tuple(
            CanonicalNode(str(i), None, NodeType.PARAGRAPH, text, text, SourceLocator(page=i))
            for i in (1, 2, 3)
        ),
        parser_revision="test",
        normalization_revision="test",
        content_checksum=hashlib.sha256(text.encode()).hexdigest(),
    )
    config = ChunkingConfig(
        target_tokens=2, overlap_tokens=0, min_tokens=1, max_tokens=2, parent_max_tokens=4
    )
    result = TokenAwareChunker(config, tokenizer=official_tokenizer).chunk(document, tenant_id="t")
    native = Tokenizer.from_file(str(OFFICIAL))
    assert all(
        len(native.encode(p.display_text, add_special_tokens=False).ids) <= 4
        for p in result.parent_chunks
    )


def test_bpe_character_boundaries_cannot_exceed_the_chunk_budget(official_tokenizer):
    text = (
        "备203養持维d否eed持𠮷持备a.a率:率c率持率0:🧪\n支d维\n支11支备a3😀d设支3.2c1c\n"
        "持🧪:養😀率b:b養否备 持1 c🧪0设0否d\n𠮷🧪ad0备备支2设:备332e保\n20 c\n率"
    )
    config = ChunkingConfig(target_tokens=4, overlap_tokens=1, min_tokens=1, max_tokens=4)
    native = Tokenizer.from_file(str(OFFICIAL))
    covered = set()
    for piece, start, end in _windows(text, config, official_tokenizer):
        assert piece == text[start:end].strip()
        assert len(native.encode(piece, add_special_tokens=False).ids) <= 4
        covered.update(range(start, end))
    assert all(i in covered for i, char in enumerate(text) if not char.isspace())
