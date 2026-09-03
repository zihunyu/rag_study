from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr
from ragkb.adapters.rag_stubs import (
    DeterministicBufferedGenerator,
    StaticFinalPermission,
    SyntheticEvidenceProvider,
)
from ragkb.application.qa import TrustedQAService
from ragkb.domain.rag import AnswerStatus, Evidence, QuestionDisposition
from ragkb.engineering_security.references import HMACReferenceSigner, ReferenceTokenError
from ragkb.infrastructure.rag_repository import SQLiteRAGRunRepository
from ragkb.infrastructure.reference_repository import SQLiteReferenceStore
from ragkb.infrastructure.sqlite import SQLiteDatabase


def _evidence(**overrides) -> Evidence:
    values = {
        "evidence_id": "E1",
        "chunk_id": "internal-chunk-id",
        "document_id": "internal-document-id",
        "document_version_id": "internal-version-id",
        "text": "设备保修期为三年。",
        "locator": {"page": 2},
        "valid_from_epoch": 0,
        "valid_to_epoch": 0,
        "authority_rank": 10,
        "permission_revision": 3,
        "authorized": True,
        "current_version": True,
    }
    values.update(overrides)
    return Evidence(**values)


def _service(
    tmp_path: Path,
    provider: SyntheticEvidenceProvider,
    *,
    generator: DeterministicBufferedGenerator | None = None,
    allowed: bool = True,
) -> tuple[TrustedQAService, SQLiteRAGRunRepository, HMACReferenceSigner]:
    database = SQLiteDatabase(tmp_path / "rag.sqlite3")
    repository = SQLiteRAGRunRepository(database)
    signer = HMACReferenceSigner(
        SecretStr("test-reference-signing-key-32bytes"), SQLiteReferenceStore(database)
    )
    service = TrustedQAService(
        provider,
        generator or DeterministicBufferedGenerator(),
        StaticFinalPermission(allowed),
        signer,
        repository,
    )
    return service, repository, signer


def test_answered_is_buffered_verified_and_uses_opaque_signed_citation(tmp_path: Path) -> None:
    service, repository, signer = _service(tmp_path, SyntheticEvidenceProvider((_evidence(),)))

    result = service.ask("保修期多久？", "tenant-1", "user-1")

    assert result.status is AnswerStatus.ANSWERED
    assert result.verified is True
    assert result.answer
    assert result.citations[0].evidence_id == "E1"
    url = result.citations[0].source_url
    assert "internal-document-id" not in url
    assert "internal-chunk-id" not in url
    parts = url.split("/")
    assert signer.resolve(parts[4], parts[6], "tenant-1", "user-1") == (
        result.rag_run_id,
        "E1",
    )
    assert repository.get_evidence(result.rag_run_id, "E1") == _evidence()


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (SyntheticEvidenceProvider(), AnswerStatus.INSUFFICIENT_EVIDENCE),
        (
            SyntheticEvidenceProvider(disposition=QuestionDisposition.NEEDS_CLARIFICATION),
            AnswerStatus.NEEDS_CLARIFICATION,
        ),
        (
            SyntheticEvidenceProvider(disposition=QuestionDisposition.OUT_OF_SCOPE),
            AnswerStatus.OUT_OF_SCOPE,
        ),
        (
            SyntheticEvidenceProvider((_evidence(),), conflict_detected=True),
            AnswerStatus.CONFLICTING_EVIDENCE,
        ),
    ],
)
def test_non_answer_business_states_are_explicit_and_never_use_not_authorized(
    tmp_path: Path,
    provider: SyntheticEvidenceProvider,
    expected: AnswerStatus,
) -> None:
    service, _, _ = _service(tmp_path, provider)

    result = service.ask("question", "tenant-1", "user-1")

    assert result.status is expected
    assert result.answer is None
    assert "not_authorized" not in str(result)


@pytest.mark.parametrize(
    ("evidence", "generator", "allowed", "warning"),
    [
        (
            _evidence(authorized=False),
            DeterministicBufferedGenerator(),
            True,
            "EVIDENCE_VALIDATION_FAILED",
        ),
        (
            _evidence(),
            DeterministicBufferedGenerator(fail=True),
            True,
            "GENERATION_UNAVAILABLE_AUTHORIZED_EVIDENCE_ONLY",
        ),
        (
            _evidence(),
            DeterministicBufferedGenerator(citation_ids=("E999",)),
            True,
            "CITATION_VALIDATION_FAILED",
        ),
        (
            _evidence(),
            DeterministicBufferedGenerator(),
            False,
            "PRE_GENERATION_PERMISSION_RECHECK_FAILED",
        ),
    ],
)
def test_security_and_generation_failures_are_fail_closed(
    tmp_path: Path,
    evidence: Evidence,
    generator: DeterministicBufferedGenerator,
    allowed: bool,
    warning: str,
) -> None:
    service, _, _ = _service(
        tmp_path,
        SyntheticEvidenceProvider((evidence,)),
        generator=generator,
        allowed=allowed,
    )

    result = service.ask("question", "tenant-1", "user-1")

    assert result.status is AnswerStatus.SYSTEM_ERROR
    assert result.answer is None
    assert result.verified is False
    assert warning in result.warnings


def test_feedback_binds_run_and_all_revisions(tmp_path: Path) -> None:
    service, repository, _ = _service(tmp_path, SyntheticEvidenceProvider((_evidence(),)))
    result = service.ask("question", "tenant-1", "user-1")

    feedback = service.feedback(result.rag_run_id, "user-1", 5, "helpful", "ok")

    package = repository.get_package(result.rag_run_id)
    assert package is not None
    assert feedback.index_generation_id == package.index_generation_id
    assert feedback.retrieval_revision == package.retrieval_revision
    assert feedback.prompt_revision == package.prompt_revision
    assert feedback.model_revision == package.model_revision


def test_tampered_reference_is_rejected(tmp_path: Path) -> None:
    signer = HMACReferenceSigner(
        SecretStr("test-reference-signing-key-32bytes"),
        SQLiteReferenceStore(SQLiteDatabase(tmp_path / "reference.sqlite3")),
    )
    url = signer.source_url("run", "E1", "tenant", "user", "document")
    parts = url.split("/")

    with pytest.raises(ReferenceTokenError):
        signer.resolve(parts[4] + "x", parts[6], "tenant", "user")


def test_retrieval_or_permission_provider_failure_is_system_error_without_leak(
    tmp_path: Path,
) -> None:
    class _FailingProvider(SyntheticEvidenceProvider):
        def build_package(self, question: str, tenant_id: str, user_id: str):
            raise RuntimeError("unauthorized resource exists")

    service, _, _ = _service(tmp_path, _FailingProvider())

    result = service.ask("question", "tenant-1", "user-1")

    assert result.status is AnswerStatus.SYSTEM_ERROR
    assert result.answer is None
    assert result.evidence == ()
    assert result.warnings == ("RETRIEVAL_OR_PERMISSION_FAIL_CLOSED",)
    assert "unauthorized resource exists" not in str(result)


def test_unreferenced_generator_context_revoked_during_generation_discards_answer(
    tmp_path: Path,
) -> None:
    second = _evidence(
        evidence_id="E2",
        chunk_id="context-e2",
        document_id="document-e2",
        document_version_id="version-e2",
    )

    class _ContextRevokedPermission:
        calls = 0

        def recheck(self, evidence: tuple[Evidence, ...], **context) -> bool:
            del evidence, context
            self.calls += 1
            return self.calls == 1

    database = SQLiteDatabase(tmp_path / "rag.sqlite3")
    repository = SQLiteRAGRunRepository(database)
    service = TrustedQAService(
        SyntheticEvidenceProvider((_evidence(), second)),
        DeterministicBufferedGenerator(citation_ids=("E1",)),
        _ContextRevokedPermission(),
        HMACReferenceSigner(
            SecretStr("test-reference-signing-key-32bytes"), SQLiteReferenceStore(database)
        ),
        repository,
    )

    result = service.ask("question", "tenant-1", "user-1")

    assert result.status is AnswerStatus.SYSTEM_ERROR
    assert result.answer is None
    assert result.citations == ()
    assert result.warnings == ("FINAL_PERMISSION_RECHECK_FAILED",)


def test_pre_generation_permission_failure_never_calls_generator(tmp_path: Path) -> None:
    class _TrackingGenerator(DeterministicBufferedGenerator):
        calls = 0

        def generate(self, question: str, evidence: tuple[Evidence, ...]):
            self.calls += 1
            return super().generate(question, evidence)

    generator = _TrackingGenerator()
    service, _, _ = _service(
        tmp_path,
        SyntheticEvidenceProvider((_evidence(),)),
        generator=generator,
        allowed=False,
    )

    result = service.ask("question", "tenant-1", "user-1")

    assert result.warnings == ("PRE_GENERATION_PERMISSION_RECHECK_FAILED",)
    assert generator.calls == 0
