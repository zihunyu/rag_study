"""Current membership/session checks at retrieval and final response authorization."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from ragkb.domain.errors import RetrievalFailClosed
from ragkb.domain.rag import Evidence, EvidencePackage
from ragkb.infrastructure.accounts import AccountService


class AccountFinalPermission:
    def __init__(self, access: AccountService, delegate: Any):
        self.access, self.delegate = access, delegate

    def recheck(
        self,
        evidence: tuple[Evidence, ...],
        *,
        tenant_id: str,
        user_id: str,
        subject_scope_tokens: tuple[str, ...],
        **kwargs: Any,
    ) -> bool:
        spaces = tuple({self.access.repository.get_document_space(e.document_id) for e in evidence})
        return bool(
            tenant_id == self.access.tenant_id
            and self.access.recheck(user_id, subject_scope_tokens, spaces)
            and self.delegate.recheck(
                evidence,
                tenant_id=tenant_id,
                user_id=user_id,
                subject_scope_tokens=subject_scope_tokens,
                **kwargs,
            )
        )


class AccountEvidenceProvider:
    def __init__(self, access: AccountService, delegate: Any, default_space: str):
        self.access, self.delegate, self.default_space = access, delegate, default_space
        self.revision = delegate.revision + ":account-authorization-v1"

    def build_package(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        *,
        subject_scope_tokens: tuple[str, ...] = (),
        clearance_level: int = 0,
        space_id: str | None = None,
    ) -> EvidencePackage:
        if tenant_id != self.access.tenant_id or not self.access.recheck(
            user_id, subject_scope_tokens, (space_id or self.default_space,)
        ):
            raise RetrievalFailClosed("KNOWLEDGE_BASE_ACCESS_REVOKED")
        return cast(
            EvidencePackage,
            self.delegate.build_package(
                question,
                tenant_id,
                user_id,
                subject_scope_tokens=subject_scope_tokens,
                clearance_level=clearance_level,
                space_id=space_id or self.default_space,
            ),
        )


@contextmanager
def account_release_guard(access: AccountService, lifecycle_lock: Any) -> Iterator[None]:
    with lifecycle_lock, access.guard():
        yield
