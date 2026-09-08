"""Runtime composition of durable dialogue with the existing verified RAG service."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import PurePosixPath
from typing import Any

from ragkb.adapters.conversation_context import ContextResolverPort, bounded_history
from ragkb.application.cancellation import cancellation_scope, check_cancelled
from ragkb.application.deadlines import request_deadline
from ragkb.application.reading_scope import ReadingOptions, reading_scope
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.errors import IngestionCancelled
from ragkb.domain.ids import new_uuid7
from ragkb.infrastructure.conversations import ACTIVE_STATES, ConversationRepository
from ragkb.infrastructure.model_account import provider_operation
from ragkb.infrastructure.visual_assets import VisualAssetStore
from ragkb.infrastructure.workspace_queries import WorkspaceQueries
from ragkb.runtime_components import RuntimeComponents


class ConversationService:
    def __init__(
        self,
        runtime: RuntimeComponents,
        repository: ConversationRepository,
        resolver: ContextResolverPort,
        queries: WorkspaceQueries,
    ) -> None:
        self.runtime, self.repository, self.resolver, self.queries = (
            runtime,
            repository,
            resolver,
            queries,
        )
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rag-conversation")
        self.lock = threading.Lock()
        self.scheduled: set[str] = set()
        self.stopping = threading.Event()

    def close(self) -> None:
        self.stopping.set()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def start(
        self, turn: dict[str, Any], conversation: dict[str, Any], subject: RequestPrincipal
    ) -> None:
        with self.lock:
            if turn["state"] != "queued" or turn["id"] in self.scheduled:
                return
            self.scheduled.add(turn["id"])
        self.executor.submit(self._execute, turn, conversation, subject)

    def present(
        self,
        turn: dict[str, Any],
        conversation: dict[str, Any],
        subject: RequestPrincipal,
        *,
        references: bool = True,
    ) -> dict[str, Any]:
        if self.runtime.accounts and self.runtime.accounts.enabled:
            if not self.runtime.accounts.recheck(
                subject.user_id, subject.scope_tokens, (conversation["space_id"],)
            ):
                from ragkb.domain.uploads import ResourceNotFoundError

                raise ResourceNotFoundError(conversation["id"])
        item = {
            key: turn[key]
            for key in (
                "id",
                "conversation_id",
                "sequence_number",
                "original_question",
                "resolved_question",
                "state",
                "rag_run_id",
                "error_code",
                "cancel_requested",
                "created_at",
                "updated_at",
            )
        }
        item["reading"] = self.repository.turn(turn["id"]).get("reading", {})
        item["reading_progress"] = VisualAssetStore(self.runtime.storage).ledger.get(
            "reading", turn["id"]
        )
        result = json.loads(turn["result_json"]) if turn.get("result_json") else None
        if self.runtime.settings.auth_mode == "password":
            from ragkb.api.citation_projection import reader_report
            from ragkb.api.support import document_manager

            if not document_manager(subject, conversation["space_id"]):
                item["reading_progress"] = reader_report(item["reading_progress"] or {})
                if result:
                    result["coverage_report"] = reader_report(result.get("coverage_report") or {})
        item["result"] = result
        if not result:
            return item
        if not result.get("verified"):
            result.update(answer=None, citations=[])
            return item
        citations = result.get("citations", [])
        if not citations:
            return item
        runtime = self.runtime
        package = runtime.rag_repository.get_package(str(turn["rag_run_id"]))
        runtime.lifecycle_store.reload()
        evidence = {entry.evidence_id: entry for entry in package.evidence} if package else {}
        selected = tuple(
            evidence[c["evidence_id"]] for c in citations if c["evidence_id"] in evidence
        )
        valid = bool(
            package
            and package.tenant_id == subject.tenant_id
            and package.user_id == subject.user_id
            and len(selected) == len(citations)
            and all(
                e.authorized
                and e.current_version
                and e.valid_at(int(time.time()))
                and runtime.lifecycle_store.is_accessible(e.document_id)
                and runtime.lifecycle_store.documents[e.document_id].active_version_id
                == e.document_version_id
                for e in selected
            )
        )
        if valid and package:
            try:
                valid = runtime.qa_service.permission.recheck(
                    selected,
                    tenant_id=subject.tenant_id,
                    user_id=subject.user_id,
                    subject_scope_tokens=subject.scope_tokens,
                    clearance_level=subject.clearance_level,
                    permission_revision=package.permission_revision,
                    at_epoch=int(time.time()),
                    generation_id=package.index_generation_id,
                )
            except Exception:
                valid = False
        if not valid:
            result.update(
                answer=None, citations=[], verified=False, sources_stale=True, retryable=True
            )
            return item
        if references:
            details: dict[str, dict[str, Any]] = {}
            for citation, entry in zip(citations, selected, strict=True):
                if entry.document_id not in details:
                    rows = self.queries.documents(
                        conversation["space_id"], document_id=entry.document_id, limit=1
                    ).items
                    details[entry.document_id] = rows[0] if rows else {}
                document = details[entry.document_id]
                version = runtime.repository.get_version(entry.document_version_id)
                citation.update(
                    source_url=runtime.reference_signer.source_url(
                        str(turn["rag_run_id"]),
                        entry.evidence_id,
                        subject.tenant_id,
                        subject.user_id,
                        entry.document_id,
                    ),
                    filename=PurePosixPath(str(version.get("original_key") or "")).name
                    or document.get("filename", "来源文档"),
                    document_id=entry.document_id,
                    version_id=entry.document_version_id,
                    version_no=version["version_no"],
                    chunk_id=entry.chunk_id,
                    locator=entry.locator,
                )
                if runtime.settings.auth_mode == "password":
                    from ragkb.api.citation_projection import citation_locator

                    citation["locator"] = citation_locator(entry.locator)
        return item

    def _execute(
        self, turn: dict[str, Any], conversation: dict[str, Any], subject: RequestPrincipal
    ) -> None:
        identity, token = turn["id"], new_uuid7()
        done = threading.Event()
        lost_lease = threading.Event()
        heartbeat_thread: threading.Thread | None = None

        def heartbeat() -> None:
            while not done.wait(5):
                try:
                    if not self.repository.heartbeat(identity, token):
                        lost_lease.set()
                        return
                except Exception:
                    lost_lease.set()
                    return

        last_check = 0.0
        cancelled = False

        def cancellation() -> bool:
            nonlocal last_check, cancelled
            if self.stopping.is_set() or lost_lease.is_set():
                return True
            if time.monotonic() - last_check > 0.25:
                state = self.repository.turn(identity)
                cancelled = bool(
                    state["cancel_requested"]
                    or state["execution_token"] != token
                    or state["state"] not in ACTIVE_STATES
                )
                if self.runtime.accounts and self.runtime.accounts.enabled:
                    cancelled = cancelled or not self.runtime.accounts.recheck(
                        subject.user_id, subject.scope_tokens, (conversation["space_id"],)
                    )
                last_check = time.monotonic()
            return cancelled

        try:
            if not self.repository.claim(identity, token):
                return
            heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
            heartbeat_thread.start()
            if cancellation():
                raise IngestionCancelled("KNOWLEDGE_BASE_ACCESS_REVOKED")
            reading = ReadingOptions.model_validate(turn.get("reading") or {})
            ledger = VisualAssetStore(self.runtime.storage).ledger

            def save_reading(report: dict[str, Any]) -> None:
                ledger.put("reading", identity, report)

            with (
                cancellation_scope(cancellation),
                provider_operation("space:" + conversation["space_id"], "", "qa"),
                reading_scope(reading, save_reading),
                request_deadline(
                    self.runtime.settings.overview_timeout_seconds
                    if reading.mode != "fact"
                    else 120
                ),
            ):
                rows = self.repository.turns(
                    conversation["id"], before=turn["sequence_number"], limit=6
                )
                context = []
                for row in rows:
                    historical = self.present(row, conversation, subject, references=False)
                    result = historical["result"] or {}
                    context.append(
                        {
                            "question": row["original_question"],
                            "resolved_question": row["resolved_question"],
                            "answer": result.get("answer") if result.get("verified") else "",
                        }
                    )
                resolved = self.resolver.resolve(
                    turn["original_question"], bounded_history(context)
                )
                check_cancelled()
                if resolved.question is None:
                    self.repository.finish(
                        identity,
                        token,
                        "completed",
                        {
                            "status": "NEEDS_CLARIFICATION",
                            "rag_run_id": None,
                            "answer": None,
                            "citations": [],
                            "verified": False,
                            "retryable": False,
                            "clarification_question": resolved.clarification,
                            "clarification_fields": ["subject"],
                            "warnings": [],
                        },
                    )
                    return
                self.repository.stage(identity, token, resolved.question)
                self.runtime.lifecycle_store.reload()
                result = self.runtime.qa_service.ask(
                    resolved.question,
                    subject.tenant_id,
                    subject.user_id,
                    subject_scope_tokens=subject.scope_tokens,
                    clearance_level=subject.clearance_level,
                    space_id=conversation["space_id"],
                )
                check_cancelled()
                payload = asdict(result)
                payload.pop("evidence", None)
                if not result.verified:
                    payload.update(answer=None, citations=[])
                self.repository.finish(
                    identity,
                    token,
                    "failed" if result.retryable else "completed",
                    payload,
                    "RAG_EXECUTION_FAILED" if result.retryable else None,
                )
        except IngestionCancelled:
            self.repository.finish(identity, token, "cancelled")
        except Exception as error:
            self.repository.finish(identity, token, "failed", error=type(error).__name__[:128])
        finally:
            done.set()
            if heartbeat_thread:
                heartbeat_thread.join(timeout=1)
            with self.lock:
                self.scheduled.discard(identity)
