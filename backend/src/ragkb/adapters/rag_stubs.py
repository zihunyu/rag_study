"""Deterministic G3 RAG adapters; never real LLM acceptance evidence."""

from __future__ import annotations

import time
from typing import Any

from ragkb.domain.ids import new_uuid7
from ragkb.domain.rag import (
    DraftAnswer,
    Evidence,
    EvidencePackage,
    QuestionDisposition,
)


class SyntheticEvidenceProvider:
    revision = "synthetic-evidence:g3-v1"

    def __init__(
        self,
        evidence: tuple[Evidence, ...] = (),
        *,
        disposition: QuestionDisposition = QuestionDisposition.ANSWERABLE,
        conflict_detected: bool = False,
    ) -> None:
        self.evidence = evidence
        self.disposition = disposition
        self.conflict_detected = conflict_detected

    def build_package(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        *,
        subject_scope_tokens: tuple[str, ...] = (),
    ) -> EvidencePackage:
        del subject_scope_tokens
        return EvidencePackage(
            rag_run_id=new_uuid7(),
            tenant_id=tenant_id or "local",
            user_id=user_id,
            query=question,
            query_time_epoch=int(time.time()),
            index_generation_id="synthetic-generation:g3-v1",
            retrieval_revision="synthetic-retrieval:g3-v1",
            prompt_revision="deterministic-prompt:g3-v1",
            model_revision="deterministic-generation:g3-v1",
            permission_revision=max(
                (item.permission_revision for item in self.evidence), default=0
            ),
            evidence=self.evidence,
            disposition=self.disposition,
            conflict_detected=self.conflict_detected,
            real_acceptance=False,
        )


class DeterministicBufferedGenerator:
    revision = "deterministic-buffered-generation:g3-v1"

    def __init__(
        self,
        *,
        answer: str = "根据已验证证据，设备保修期为三年。",
        citation_ids: tuple[str, ...] = ("E1",),
        fail: bool = False,
    ) -> None:
        self.answer = answer
        self.citation_ids = citation_ids
        self.fail = fail

    def generate(self, question: str, evidence: tuple[Evidence, ...]) -> DraftAnswer:
        if self.fail:
            raise RuntimeError("synthetic generation failure")
        return DraftAnswer(self.answer, self.citation_ids)


class StaticFinalPermission:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def recheck(
        self,
        evidence: tuple[Evidence, ...],
        *,
        tenant_id: str,
        user_id: str,
        subject_scope_tokens: tuple[str, ...],
        permission_revision: int,
        at_epoch: int,
    ) -> bool:
        del subject_scope_tokens
        return bool(
            self.allowed
            and tenant_id
            and user_id
            and all(
                item.authorized
                and item.current_version
                and item.valid_at(at_epoch)
                and item.locator
                and item.permission_revision <= permission_revision
                for item in evidence
            )
        )


class LifecycleAwareFinalPermission:
    def __init__(self, lifecycle_store: Any, tenant_id: str) -> None:
        self.lifecycle_store = lifecycle_store
        self.tenant_id = tenant_id

    def recheck(
        self,
        evidence: tuple[Evidence, ...],
        *,
        tenant_id: str,
        user_id: str,
        subject_scope_tokens: tuple[str, ...],
        permission_revision: int,
        at_epoch: int,
    ) -> bool:
        del subject_scope_tokens
        return bool(
            tenant_id == self.tenant_id
            and user_id
            and all(
                item.authorized
                and item.current_version
                and item.valid_at(at_epoch)
                and item.locator
                and item.permission_revision <= permission_revision
                and (record := self.lifecycle_store.documents.get(item.document_id)) is not None
                and self.lifecycle_store.is_accessible(item.document_id)
                and record.active_version_id == item.document_version_id
                and item.permission_revision == record.acl_revision
                for item in evidence
            )
        )
