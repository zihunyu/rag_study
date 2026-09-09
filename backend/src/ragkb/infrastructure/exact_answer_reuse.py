"""Persistent exact-result reuse, fenced by the complete current corpus snapshot."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ragkb.application.qa import TrustedQAService

from pymysql.err import MySQLError  # type: ignore[import-untyped]
from redis.exceptions import RedisError

from ragkb.application.acceptance_budget import fresh_answer_required
from ragkb.application.qa_performance import record_event
from ragkb.application.reading_scope import options
from ragkb.domain.errors import RAGError
from ragkb.domain.ids import new_uuid7
from ragkb.domain.rag import AnswerStatus, AskResult, Citation
from ragkb.domain.retrieval import RetrievalHealth
from ragkb.infrastructure.rag_repository import _package, _result


class ExactAnswerReuse:
    def __init__(
        self,
        cache: Any,
        snapshot: Callable[[str, str | None], str | None],
        *,
        config_revision: str,
        ttl_seconds: int,
    ) -> None:
        self.cache, self.snapshot = cache, snapshot
        self.config_revision, self.ttl_seconds = config_revision, ttl_seconds

    def key(self, question: str, tenant: str, user: str, **scope: Any) -> str | None:
        # Relative-time interpretation cannot be frozen independently of the clock.
        if re.search(
            "今天|明天|昨天|现在|此刻|本周|本月|今年|近日|\\b(?:today|tomorrow|yesterd"
            "ay|now|this week|this month|this year)\\b",
            question,
            re.I,
        ):
            return None
        snapshot = self.snapshot(tenant, scope.get("space_id"))
        if snapshot is None:
            return None
        return hashlib.sha256(
            json.dumps(
                {
                    "revision": "exact-answer-v1",
                    "snapshot": snapshot,
                    "configuration": self.config_revision,
                    "question": question,
                    "tenant": tenant,
                    "user": user,
                    "scope": scope,
                    "reading": options.get().model_dump(mode="json"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def execute(
        self, service: TrustedQAService, question: str, tenant: str, user: str, **scope: Any
    ) -> AskResult:
        if fresh_answer_required():
            record_event("cache", cache="verified_result", outcome="bypass_acceptance")
            return service._ask(question, tenant, user, **scope)
        try:
            key = self.key(question, tenant, user, **scope)
            value = self.cache.get_json("verified-result-v1", key) if key else None
        except (RedisError, MySQLError, ValueError, OSError, RAGError):
            key, value = None, None
        if value:
            try:
                package, prior = _package(value["package"]), _result(value["result"])
                if (
                    not prior.verified
                    or prior.status is not AnswerStatus.ANSWERED
                    or not prior.answer
                    or prior.degraded
                    or package.retrieval_health is not RetrievalHealth.HEALTHY
                    or package.query != question
                    or package.tenant_id != tenant
                    or package.user_id != user
                    or not prior.citations
                    or any(
                        not e.authorized
                        or not e.current_version
                        or not e.valid_at(int(time.time()))
                        for e in package.evidence
                    )
                ):
                    raise ValueError("EXACT_CACHE_RECEIPT_INVALID")
                ids = {c.evidence_id for c in prior.citations}
                if not ids.issubset({e.evidence_id for e in package.evidence}):
                    raise ValueError("EXACT_CACHE_CITATIONS_INVALID")
            except (KeyError, TypeError, ValueError):
                value = None
        if value:
            # Reissue all references for this run, inside the normal release guard.
            # Check the FULL snapshot again, including previously uncited conflicts.
            with service.response_release_guard():
                try:
                    release_key = self.key(question, tenant, user, **scope)
                except (RedisError, MySQLError, ValueError, OSError, RAGError):
                    release_key = None
                if key == release_key and service._permission_recheck(
                    package, scope.get("subject_scope_tokens", ()), scope.get("clearance_level", 0)
                ):
                    report = {k: v for k, v in prior.coverage_report.items() if k != "performance"}
                    package = replace(
                        package,
                        rag_run_id=new_uuid7(),
                        query_time_epoch=int(time.time()),
                        diagnostics={},
                        coverage_report=report,
                    )
                    citations = tuple(
                        Citation(
                            e.evidence_id,
                            service.references.source_url(
                                package.rag_run_id, e.evidence_id, tenant, user, e.document_id
                            ),
                            e.locator,
                        )
                        for e in package.evidence
                        if e.evidence_id in ids
                    )
                    record_event(
                        "cache",
                        cache="verified_result",
                        outcome="hit",
                        origin_run_id=prior.rag_run_id,
                    )
                    return service._save(
                        package,
                        AnswerStatus.ANSWERED,
                        answer=prior.answer,
                        citations=citations,
                        warnings=prior.warnings,
                        verified=True,
                    )
            record_event("cache", cache="verified_result", outcome="invalidated_at_release")
        else:
            record_event(
                "cache", cache="verified_result", outcome="miss" if key else "bypass_snapshot"
            )
        result = service._ask(question, tenant, user, **scope)
        if (
            key
            and result.verified
            and result.status is AnswerStatus.ANSWERED
            and not result.degraded
        ):
            try:
                with service.response_release_guard():
                    if key == self.key(question, tenant, user, **scope):
                        saved_package = service.repository.get_package(result.rag_run_id)
                        if saved_package and service._permission_recheck(
                            saved_package,
                            scope.get("subject_scope_tokens", ()),
                            scope.get("clearance_level", 0),
                        ):
                            self.cache.set_json(
                                "verified-result-v1",
                                key,
                                {
                                    "package": asdict(replace(saved_package, diagnostics={})),
                                    "result": asdict(result),
                                },
                                self.ttl_seconds,
                            )
            except (RedisError, MySQLError, ValueError, OSError, RAGError):
                pass  # Cache writes do not change the already verified answer.
        return result
