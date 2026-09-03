from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pydantic import SecretStr
from ragkb.adapters.rag_stubs import StaticFinalPermission, SyntheticEvidenceProvider
from ragkb.application.qa import InMemoryVerifiedAnswerCache, TrustedQAService
from ragkb.domain.rag import DraftAnswer, Evidence
from ragkb.engineering_security.references import HMACReferenceSigner
from ragkb.infrastructure.rag_repository import SQLiteRAGRunRepository
from ragkb.infrastructure.reference_repository import SQLiteReferenceStore
from ragkb.infrastructure.sqlite import SQLiteDatabase


class _CountingGenerator:
    revision = "counting-model:v1"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, question, evidence):
        self.calls += 1
        return DraftAnswer("三年", ("E1",))


def _evidence() -> Evidence:
    return Evidence(
        "E1", "chunk", "document", "version", "保修期三年", {"page": 1}, 0, 0, 1, 1, True, True
    )


def test_only_verified_answer_is_cached_and_revision_change_invalidates(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "cache.sqlite3")
    repository = SQLiteRAGRunRepository(database)
    generator = _CountingGenerator()
    service = TrustedQAService(
        SyntheticEvidenceProvider((_evidence(),)),
        generator,
        StaticFinalPermission(),
        HMACReferenceSigner(
            SecretStr("cache-test-signing-secret-32bytes"), SQLiteReferenceStore(database)
        ),
        repository,
        InMemoryVerifiedAnswerCache(),
    )

    assert service.ask("保修期？", "tenant", "user").verified
    assert service.ask("保修期？", "tenant", "user").verified
    assert generator.calls == 1

    provider = SyntheticEvidenceProvider((replace(_evidence(), text="新版本保修期五年"),))
    service.evidence_provider = provider
    assert service.ask("保修期？", "tenant", "user").verified
    assert generator.calls == 2
