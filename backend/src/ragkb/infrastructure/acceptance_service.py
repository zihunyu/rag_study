"""Persistent acceptance execution integrating the normal QA pipeline and adapters."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any

from ragkb.adapters.auth import AuthorizationError
from ragkb.adapters.conversation_context import bounded_history
from ragkb.adapters.model_http import HttpxJsonTransport
from ragkb.application.acceptance_budget import AcceptancePaused, acceptance_budget
from ragkb.application.acceptance_trace import capture_content
from ragkb.application.cancellation import cancellation_scope
from ragkb.application.deadlines import request_deadline
from ragkb.application.reading_scope import ReadingOptions, reading_scope
from ragkb.domain.acceptance import AcceptanceCase, fingerprint, mechanical_checks
from ragkb.domain.acceptance_points import (
    EVALUATION_REVISION,
    compare_points,
    criteria_for,
    effective_points,
    pending_points,
    point_summary,
)
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.errors import RAGError
from ragkb.domain.ids import new_uuid7
from ragkb.domain.rag import Evidence
from ragkb.domain.uploads import IdempotencyConflictError, ResourceNotFoundError
from ragkb.infrastructure.acceptance_assistance import AcceptanceAssistance
from ragkb.infrastructure.acceptance_lineage import (
    AcceptanceLineage,
    compare_lineage,
    overlay_review,
)
from ragkb.infrastructure.acceptance_repository import AcceptanceRepository
from ragkb.infrastructure.conversation_service import ConversationService
from ragkb.infrastructure.model_account import background_requests, provider_operation
from ragkb.infrastructure.parsing_acceptance import ParsingAcceptance
from ragkb.runtime_components import RuntimeComponents


class AcceptanceService:
    def __init__(self, runtime: RuntimeComponents, conversation: ConversationService) -> None:
        self.runtime, self.conversation = runtime, conversation
        self.repository = AcceptanceRepository(conversation.queries.db, runtime.tenant_id)
        self.assistance = AcceptanceAssistance(runtime)
        self.parsing = ParsingAcceptance(self)
        self.lineage = AcceptanceLineage(self)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qa-acceptance")
        self.stopping = threading.Event()
        self.scheduled: set[str] = set()
        self.schedule_lock = threading.Lock()
        root = Path(__file__).parents[1]
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*.py")):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
        self.code_revision = digest.hexdigest()

    def close(self) -> None:
        self.stopping.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
        if self.runtime.model_transport is None and isinstance(
            self.assistance.model._transport, HttpxJsonTransport
        ):
            self.assistance.model._transport.close()

    def authorize(self, subject: RequestPrincipal, space: str) -> None:
        from ragkb.api.support import document_manager

        if (
            subject.tenant_id != self.runtime.tenant_id
            or (self.runtime.repository.get_space(space)["tenant_id"] != subject.tenant_id)
            or not document_manager(subject, space)
        ):
            raise ResourceNotFoundError(space)
        if self.runtime.accounts and self.runtime.accounts.enabled:
            self.runtime.accounts.require_space(subject, space, manage=True)
            if not self.runtime.accounts.recheck(subject.user_id, subject.scope_tokens, (space,)):
                raise AuthorizationError("ACCEPTANCE_ACCESS_REVOKED")

    def validate_case(self, subject: RequestPrincipal, space: str, case: AcceptanceCase) -> None:
        self.authorize(subject, space)
        if any(
            len(s) > 2000 or not s.strip()
            for s in [
                *case.required_points,
                *case.forbidden_claims,
                *case.required_source_documents,
                *case.required_retrieved_documents,
            ]
        ):
            raise ValueError("CASE_POINT_INVALID")
        for identity in {
            *case.reading.document_ids,
            *case.required_source_documents,
            *case.required_retrieved_documents,
            *(d for h in case.history for d in h.reading.document_ids),
            *(s.document_id for p in case.criteria for s in p.sources),
        }:
            self.authorize(subject, self.runtime.repository.get_document_space(identity))
        bindings = [s.model_dump() for p in case.criteria for s in p.sources]
        if bindings:
            self.assistance.bindings(subject, bindings)
        for point in case.criteria:
            self.lineage.original(subject, space, point.model_dump(mode="json"))
        if case.criteria and (case.required_points or case.forbidden_claims):
            raise ValueError("USE_CRITERIA_OR_LEGACY_POINTS")
        if (
            case.review_state == "confirmed"
            and case.criteria
            and any(p.kind == "required" and not p.sources for p in case.criteria)
        ):
            raise ValueError("CONFIRMED_POINTS_REQUIRE_SOURCES")
        if case.review_state == "confirmed" and not case.source_notes.strip():
            raise ValueError("CONFIRMED_CASE_REQUIRES_SOURCE_NOTES")
        if (
            case.review_state == "confirmed"
            and case.expected_status == "answered"
            and not (
                (case.required_points or case.criteria)
                and (case.required_source_documents or bindings)
            )
        ):
            raise ValueError("ANSWERED_CASE_REQUIRES_POINTS_AND_SOURCES")

    def snapshot(
        self, subject: RequestPrincipal, space: str, cases: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.authorize(subject, space)
        documents = self.conversation.queries.documents(space, limit=1000)
        if documents.next_key:
            raise ValueError("ACCEPTANCE_SOURCE_LIMIT_1000")
        self.runtime.lifecycle_store.reload()
        ids = {r["document_id"] for r in documents.items}
        for row in cases:
            case = AcceptanceCase.model_validate(row["payload"])
            self.validate_case(subject, space, case)
            ids.update(case.reading.document_ids)
            ids.update(case.required_source_documents)
            ids.update(case.required_retrieved_documents)
            ids.update(d for h in case.history for d in h.reading.document_ids)
            ids.update(s.document_id for p in case.criteria for s in p.sources)
        sources = []
        for identity in sorted(ids):
            record = self.runtime.lifecycle_store.documents.get(identity)
            if not record:
                raise ResourceNotFoundError(identity)
            sources.append(
                {
                    "document_id": identity,
                    "space_id": self.runtime.repository.get_document_space(identity),
                    "version_id": record.active_version_id,
                    "visible": record.visible,
                    "state": str(record.lifecycle_state),
                    "acl_revision": record.acl_revision,
                    "tombstoned": record.tombstoned,
                }
            )
        settings = self.runtime.settings
        config = {
            k: getattr(settings, k)
            for k in (
                "rag_runtime_profile",
                "llm_model",
                "verifier_model",
                "verifier_timeout_seconds",
                "verifier_total_timeout_seconds",
                "verifier_condition_batch_size",
                "verifier_condition_batch_characters",
                "verifier_max_condition_batches",
                "verifier_condition_parallelism",
                "qa_visual_dependency_planning",
                "ocr_query_parallelism",
                "ocr_max_concurrency",
                "qa_exact_result_cache_enabled",
                "embedding_model",
                "reranker_model",
                "retrieval_active_generation_id",
                "overview_timeout_seconds",
                "qa_fact_timeout_seconds",
                "model_http_max_retries",
                "real_provider_calls_enabled",
            )
        }
        # Freeze every numeric tuning/budget/concurrency parameter of the QA
        # providers, not just model names. Never serialize credentials or URLs.
        for key in type(settings).model_fields:
            value = getattr(settings, key)
            if key.startswith(
                (
                    "llm_",
                    "verifier_",
                    "retrieval_",
                    "embedding_",
                    "reranker_",
                    "ocr_",
                    "overview_",
                    "qa_",
                    "model_http_",
                    "model_account_",
                )
            ):
                if isinstance(value, (bool, int, float)) or key.endswith(("_model", "_revision")):
                    config[key] = value
        return {
            "sources": sources,
            "original_standards": {
                p.original_standard_id: self.parsing.record(subject, space, p.original_standard_id)[
                    "revision"
                ]
                for row in cases
                for p in AcceptanceCase.model_validate(row["payload"]).criteria
                if p.original_standard_id
            },
            "config": config,
            "code_revision": self.code_revision,
            "evaluation_revision": EVALUATION_REVISION,
            "answer_cache": "bypassed",
            "scope_hash": fingerprint(
                sorted(t for t in subject.scope_tokens if not t.startswith("auth-session:"))
            ),
            "release": asdict(
                self.runtime.retrieval_release.current_release(subject.tenant_id, space)
            ),
        }

    def create_run(
        self,
        subject: RequestPrincipal,
        space: str,
        case_ids: list[str],
        key: str,
        name: str,
        limit: int,
        review_mode: str = "manual",
        repeat_count: int = 1,
    ) -> dict[str, Any]:
        self.authorize(subject, space)
        if not 1 <= repeat_count <= 5:
            raise ValueError("REPEAT_COUNT_MUST_BE_1_TO_5")
        available = {r["id"]: r for r in self.repository.cases(space)}
        if (
            not case_ids
            or len(case_ids) != len(set(case_ids))
            or not set(case_ids) <= available.keys()
        ):
            raise ValueError("SELECT_VALID_CASES")
        cases = [
            {"id": i, "revision": available[i]["revision"], "payload": available[i]["payload"]}
            for i in case_ids
        ]
        if any(
            c["payload"]["archived"] or c["payload"]["review_state"] != "confirmed" for c in cases
        ):
            raise ValueError("ONLY_CONFIRMED_ACTIVE_CASES_CAN_RUN")
        snapshot = self.snapshot(subject, space, cases)
        return self.repository.create_run(
            space,
            subject.user_id,
            key,
            {
                "name": name,
                "cases": cases,
                "snapshot": snapshot,
                "review_mode": review_mode,
                "repeat_count": repeat_count,
            },
            limit,
        )

    def create_generation(
        self,
        subject: RequestPrincipal,
        space: str,
        sources: list[dict[str, Any]],
        count: int,
        key: str,
        limit: int,
    ) -> dict[str, Any]:
        self.authorize(subject, space)
        if any(
            self.runtime.repository.get_document_space(s["document_id"]) != space for s in sources
        ):
            raise ResourceNotFoundError(space)
        bound = self.assistance.bindings(subject, sources)
        if sum(len(s["quote"]) for s in bound) > 32000:
            raise ValueError("SELECT_SMALLER_SOURCE_SCOPE_32000_CHARACTERS")
        docs = sorted({s["document_id"] for s in bound})
        spec = AcceptanceCase(
            key="候选题生成",
            question="从选定原文生成候选题",
            reading=ReadingOptions(mode="fact", document_ids=tuple(docs)),
        ).model_dump(mode="json")
        cases = [{"id": "generation", "revision": 1, "payload": spec}]
        payload = {
            "kind": "case_generation",
            "name": "从文档生成候选题",
            "cases": cases,
            "snapshot": self.snapshot(subject, space, cases),
            "generation_sources": bound,
            "requested_count": count,
            "review_mode": "manual",
        }
        return self.repository.create_run(space, subject.user_id, key, payload, limit)

    def start(self, subject: RequestPrincipal, space: str, identity: str) -> dict[str, Any]:
        self.authorize(subject, space)
        run = self.repository.run(space, identity)
        run.pop("execution_token", None)
        with self.schedule_lock:
            run["queued"] = run["state"] == "ready" and identity in self.scheduled
        if run["actor_id"] != subject.user_id:
            raise AuthorizationError("ONLY_RUN_CREATOR_CAN_RESUME")
        if run["state"] in {"running", "completed"}:
            return self.detail(subject, space, identity)
        if self.snapshot(subject, space, run["payload"]["cases"]) != run["payload"]["snapshot"]:
            raise IdempotencyConflictError("RUN_SNAPSHOT_CHANGED_CREATE_NEW_RUN")
        # Claim in the worker, never while waiting in the local executor queue.
        with self.schedule_lock:
            if identity in self.scheduled:
                return run
            if len(self.scheduled) >= 8:
                raise IdempotencyConflictError("ACCEPTANCE_QUEUE_FULL")
            self.scheduled.add(identity)
        self.executor.submit(self._dispatch, subject, space, identity)
        return run

    def _dispatch(self, subject: RequestPrincipal, space: str, identity: str) -> None:
        try:
            self._execute(subject, space, identity)
        finally:
            with self.schedule_lock:
                self.scheduled.discard(identity)

    def _execute(self, subject: RequestPrincipal, space: str, identity: str) -> None:
        token = new_uuid7()
        repo = self.repository
        if self.stopping.is_set() or not repo.claim(space, identity, token):
            return
        done, lost = threading.Event(), threading.Event()

        def heartbeat() -> None:
            while not done.wait(5):
                try:
                    if not repo.heartbeat(identity, token):
                        lost.set()
                        return
                except Exception:
                    lost.set()
                    return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        attempt = ""
        payload: dict[str, Any] = {}
        try:
            run = repo.run(space, identity)
            original = run["payload"]["snapshot"]
            completed = {
                (a["case_id"], a["payload"].get("repetition", 1))
                for a in repo.attempts(identity)
                if a["state"] in {"completed", "failed"}
            }
            schedule = [
                (case, repetition)
                for repetition in range(1, run["payload"].get("repeat_count", 1) + 1)
                for case in run["payload"]["cases"]
            ]
            for case, repetition in schedule:
                if (case["id"], repetition) in completed:
                    continue
                if self.stopping.is_set() or lost.is_set():
                    raise AcceptancePaused("RUN_PAUSED")
                if self.snapshot(subject, space, run["payload"]["cases"]) != original:
                    raise AcceptancePaused("RUN_SNAPSHOT_CHANGED_CREATE_NEW_RUN")
                attempt = repo.begin_attempt(identity, token, case["id"], repetition=repetition)
                prior = [
                    a
                    for a in repo.attempts(identity)
                    if a["case_id"] == case["id"]
                    and a["id"] != attempt
                    and a["payload"].get("repetition", 1) == repetition
                    and a["payload"].get("qa_complete")
                ]
                payload = (
                    {**prior[-1]["payload"], "reused_qa_receipt": True}
                    if prior
                    else {"steps": [], "checks": [], "point_results": [], "error_code": ""}
                )
                payload["error_code"] = ""
                payload["repetition"] = repetition
                started = time.time()

                def reserve() -> None:
                    self.authorize(subject, space)
                    if self.stopping.is_set() or lost.is_set():
                        raise AcceptancePaused("RUN_PAUSED")
                    repo.reserve(identity, token)

                with (
                    acceptance_budget(reserve, partial(repo.observe, identity, attempt)),
                    background_requests(),
                    cancellation_scope(lambda: self.stopping.is_set() or lost.is_set()),
                ):
                    if run["payload"].get("kind") == "case_generation":
                        payload["phase"] = "candidate_generation"
                        bound = self.assistance.bindings(
                            subject, run["payload"]["generation_sources"]
                        )
                        with (
                            provider_operation(
                                "acceptance:" + identity, attempt, "candidate_generation"
                            ),
                            request_deadline(self.runtime.settings.verifier_total_timeout_seconds),
                        ):
                            payload["generated_cases"] = self.assistance.generate(
                                bound, run["payload"]["requested_count"], identity
                            )
                        self.authorize(subject, space)
                        if self.snapshot(subject, space, run["payload"]["cases"]) != original:
                            raise AcceptancePaused("RUN_SNAPSHOT_CHANGED_CREATE_NEW_RUN")
                        payload["elapsed_seconds"] = round(time.time() - started, 3)
                        repo.finish_attempt(identity, token, attempt, "completed", payload)
                        attempt = ""
                        continue
                    history: list[dict[str, Any]] = []
                    spec = case["payload"]
                    questions = [
                        *spec["history"],
                        {"question": spec["question"], "reading": spec["reading"]},
                    ]
                    if payload.get("qa_complete"):
                        step = payload["steps"][-1]
                    else:
                        payload["phase"] = "qa"
                    for index, question in enumerate(
                        [] if payload.get("qa_complete") else questions
                    ):
                        self.authorize(subject, space)
                        reading = ReadingOptions.model_validate(question["reading"])
                        progress: dict[str, Any] = {}
                        with (
                            capture_content() as content_trace,
                            provider_operation("acceptance:" + identity, attempt, "qa"),
                            reading_scope(reading, progress.update),
                            request_deadline(
                                self.runtime.settings.overview_timeout_seconds
                                if reading.mode != "fact"
                                else self.runtime.settings.qa_fact_timeout_seconds
                            ),
                        ):
                            resolved = self.conversation.resolver.resolve(
                                question["question"], bounded_history(history)
                            )
                            if resolved.question is None:
                                step = {
                                    "result": {
                                        "status": "needs_clarification",
                                        "answer": None,
                                        "citations": [],
                                        "verified": False,
                                    },
                                    "evidence": [],
                                    "diagnostics": {},
                                    "reading_progress": progress,
                                }
                            else:
                                result = self.runtime.qa_service.ask(
                                    resolved.question,
                                    subject.tenant_id,
                                    subject.user_id,
                                    subject_scope_tokens=subject.scope_tokens,
                                    clearance_level=subject.clearance_level,
                                    space_id=space,
                                )
                                package = self.runtime.rag_repository.get_package(result.rag_run_id)
                                result_payload = asdict(result)
                                result_payload.pop("evidence", None)
                                # Signed user-bound URLs are generated on demand elsewhere.
                                for citation in result_payload.get("citations", []):
                                    citation.pop("source_url", None)
                                step = {
                                    "result": result_payload,
                                    "evidence": [asdict(e) for e in package.evidence]
                                    if package
                                    else [],
                                    "diagnostics": {
                                        **(package.diagnostics if package else {}),
                                        "content_trace": content_trace,
                                    },
                                    "reading_progress": progress,
                                    "revisions": {
                                        k: getattr(package, k)
                                        for k in (
                                            "model_revision",
                                            "prompt_revision",
                                            "verifier_revision",
                                            "retrieval_revision",
                                        )
                                    }
                                    if package
                                    else {},
                                }
                            # Persist the completed QA receipt before the deadline
                            # context exits: expiry must not orphan its diagnostics.
                            payload["steps"].append(step)
                            repo.finish_attempt(identity, token, attempt, "running", payload)
                        if step["result"].get("retryable"):
                            limited = "MODEL_PROVIDER_RATE_LIMITED" in step["result"].get(
                                "warnings", []
                            )
                            raise AcceptancePaused(
                                "MODEL_PROVIDER_RATE_LIMITED"
                                if limited
                                else "MODEL_SERVICE_UNAVAILABLE"
                            )
                        if (
                            index < len(questions) - 1
                            and step["result"].get("status") != "answered"
                        ):
                            raise AcceptancePaused("HISTORY_PREREQUISITE_FAILED")
                        history.append(
                            {
                                "question": question["question"],
                                "resolved_question": resolved.question or question["question"],
                                "answer": step["result"].get("answer")
                                if step["result"].get("verified")
                                else "",
                            }
                        )
                    allowed = {
                        s["document_id"]
                        for s in original["sources"]
                        if s["space_id"] == space
                        and s["visible"]
                        and not s["tombstoned"]
                        and s["version_id"]
                    }
                    if spec["reading"]["document_ids"]:
                        allowed &= set(spec["reading"]["document_ids"])
                    payload["checks"] = mechanical_checks(
                        spec, step["result"], step["evidence"], allowed
                    )
                    if not payload.get("qa_complete"):
                        payload["qa_elapsed_seconds"] = round(time.time() - started, 3)
                    payload["qa_complete"] = True
                    if "content_lineage" not in payload:
                        payload["content_lineage"] = self.lineage.report(subject, space, spec, step)
                    if not payload.get("point_results"):
                        payload["point_results"] = pending_points(spec)
                    repo.finish_attempt(identity, token, attempt, "running", payload)
                    if run["payload"].get("review_mode") == "assisted":
                        payload["phase"] = "semantic_review"
                        all_points = criteria_for(spec)
                        unbound = {
                            p["id"]
                            for p in all_points
                            if not p["sources"] and p["kind"] != "relevance"
                        }
                        payload["point_results"] = [
                            {
                                **p,
                                "note": "尚未绑定可定位原文，请人工复核或补充案例依据。",
                                "origin": "unbound",
                            }
                            if p["point_id"] in unbound
                            else p
                            for p in payload["point_results"]
                        ]
                        done_ids = {
                            p["point_id"]
                            for p in payload["point_results"]
                            if p.get("origin") in {"model", "unbound"}
                        }
                        remaining = [p for p in all_points if p["id"] not in done_ids]
                        batches: list[list[dict[str, Any]]] = []
                        for point in remaining:
                            if (
                                not batches
                                or len(batches[-1]) >= 6
                                or len(json.dumps([*batches[-1], point], ensure_ascii=False))
                                > 38000
                            ):
                                batches.append([])
                            batches[-1].append(point)
                        for batch in batches:
                            self.assistance.bindings(
                                subject, [s for p in batch for s in p["sources"]]
                            )
                            with (
                                provider_operation(
                                    "acceptance:" + identity, attempt, "acceptance_review"
                                ),
                                request_deadline(
                                    self.runtime.settings.verifier_total_timeout_seconds
                                ),
                            ):
                                reviewed = self.assistance.review_batch(
                                    spec["question"], step["result"].get("answer") or "", batch
                                )
                            updates = {p["point_id"]: p for p in reviewed}
                            payload["point_results"] = [
                                updates.get(p["point_id"], p) for p in payload["point_results"]
                            ]
                            repo.finish_attempt(identity, token, attempt, "running", payload)
                    payload["point_summary"] = point_summary(payload["point_results"])
                    payload["phase"] = "completed"
                if self.snapshot(subject, space, run["payload"]["cases"]) != original:
                    raise AcceptancePaused("RUN_SNAPSHOT_CHANGED_CREATE_NEW_RUN")
                payload["elapsed_seconds"] = round(time.time() - started, 3)
                repo.finish_attempt(
                    identity,
                    token,
                    attempt,
                    "completed" if all(c["passed"] for c in payload["checks"]) else "failed",
                    payload,
                )
                attempt = ""
            repo.finish_run(identity, token, "completed")
        except Exception as error:
            reason = (
                str(error)
                if isinstance(error, (AcceptancePaused, RAGError))
                else type(error).__name__
            )
            if attempt:
                payload["error_code"] = reason[:191]
                payload["elapsed_seconds"] = round(time.time() - started, 3)
                repo.finish_attempt(identity, token, attempt, "incomplete", payload)
            repo.finish_run(identity, token, "paused", reason[:191])
        finally:
            done.set()
            thread.join(timeout=1)

    def detail(
        self, subject: RequestPrincipal, space: str, identity: str, *, attempt_id: str = ""
    ) -> dict[str, Any]:
        self.authorize(subject, space)
        run = self.repository.run(space, identity)
        run.pop("execution_token", None)
        with self.schedule_lock:
            run["queued"] = run["state"] == "ready" and identity in self.scheduled
        authorized_spaces = {space}
        for source in run["payload"]["snapshot"]["sources"]:
            source_space = self.runtime.repository.get_document_space(source["document_id"])
            if source_space not in authorized_spaces:
                self.authorize(subject, source_space)
                authorized_spaces.add(source_space)
        attempts = self.repository.attempts(identity)
        if attempt_id:
            attempts = [a for a in attempts if a["id"] == attempt_id]
        self.runtime.lifecycle_store.reload()
        document_access: dict[tuple[str, str], bool] = {}
        hidden_cases: set[str] = set()
        for case in run["payload"]["cases"]:
            try:
                self.validate_case(subject, space, AcceptanceCase.model_validate(case["payload"]))
            except (ValueError, AuthorizationError, ResourceNotFoundError):
                hidden_cases.add(case["id"])
                case["payload"] = AcceptanceCase(
                    key=case["payload"]["key"],
                    question="案例来源已失效，请以当前有效资料重新确认。",
                ).model_dump(mode="json")
        if run["payload"].get("kind") == "case_generation":
            try:
                self.assistance.bindings(subject, run["payload"]["generation_sources"])
            except (ValueError, AuthorizationError, ResourceNotFoundError):
                hidden_cases.update(c["id"] for c in run["payload"]["cases"])
                run["payload"]["generation_sources"] = []
        for attempt in attempts:
            for step in attempt["payload"].get("steps", []):
                diagnostics = step.pop("diagnostics", {})
                failure = diagnostics.get("failure", {})
                step["failure"] = {k: failure[k] for k in ("stage", "code") if k in failure}
                step["diagnostics_available"] = bool(diagnostics)
                step["failure"]["condition_ids"] = [
                    c.get("id")
                    for c in failure.get("verification", {}).get("condition_checks", [])
                    if c.get("status") == "missing"
                ]
                step["failure"]["conflicting_evidence_ids"] = failure.get("verification", {}).get(
                    "conflicting_evidence_ids", []
                )
                detail = failure.get("detail", {})
                for key in ("condition_id", "evidence_id", "reason", "verification_stage"):
                    if isinstance(detail.get(key), str):
                        step["failure"][key] = detail[key][:200]
                for key in ("batch_number", "batch_count", "completed_batches", "condition_count"):
                    if type(detail.get(key)) is int:
                        step["failure"][key] = detail[key]
                # Recheck current source permissions before releasing saved evidence or answers.
                visible = True
                for evidence in step["evidence"]:
                    source_key = (evidence["document_id"], evidence["document_version_id"])
                    if source_key in document_access:
                        visible = visible and document_access[source_key]
                        continue
                    source_visible = True
                    try:
                        self.authorize(
                            subject,
                            self.runtime.repository.get_document_space(evidence["document_id"]),
                        )
                        lifecycle = self.runtime.lifecycle_store.documents[evidence["document_id"]]
                        if (
                            not lifecycle.visible
                            or lifecycle.tombstoned
                            or lifecycle.active_version_id != evidence["document_version_id"]
                        ):
                            source_visible = False
                    except (AuthorizationError, ResourceNotFoundError, KeyError):
                        source_visible = False
                    document_access[source_key] = source_visible
                    visible = visible and source_visible
                if visible and step["evidence"]:
                    try:
                        release = run["payload"]["snapshot"]["release"]
                        visible = self.runtime.qa_service.permission.recheck(
                            tuple(Evidence(**e) for e in step["evidence"]),
                            tenant_id=subject.tenant_id,
                            user_id=subject.user_id,
                            subject_scope_tokens=subject.scope_tokens,
                            clearance_level=subject.clearance_level,
                            permission_revision=release["active_permission_revision"],
                            generation_id=release["active_generation_id"],
                            at_epoch=int(time.time()),
                        )
                    except Exception:
                        visible = False
                if not visible:
                    step["evidence"] = []
                    step["result"] = {
                        "status": "sources_unavailable",
                        "answer": None,
                        "citations": [],
                    }
                    step["reading_progress"] = {}
                    step["failure"] = {"code": "SOURCE_ACCESS_CHANGED"}
                else:
                    step["evidence"] = [
                        {
                            k: e.get(k)
                            for k in (
                                "evidence_id",
                                "document_id",
                                "document_version_id",
                                "text",
                                "display_text",
                                "locator",
                            )
                        }
                        for e in step["evidence"]
                    ]
            if attempt["case_id"] in hidden_cases or any(
                s["result"].get("status") == "sources_unavailable"
                for s in attempt["payload"].get("steps", [])
            ):
                attempt["payload"] = {
                    "sources_unavailable": True,
                    "steps": [
                        {
                            "result": {
                                "status": "sources_unavailable",
                                "answer": None,
                                "citations": [],
                            },
                            "evidence": [],
                            "reading_progress": {},
                            "failure": {"code": "SOURCE_ACCESS_CHANGED"},
                        }
                        for _ in attempt["payload"].get("steps", [])
                    ],
                    "checks": [],
                    "point_results": [],
                    "generated_cases": [],
                }
                attempt["reviews"] = []
                attempt["verdict"] = "incomplete"
            attempt["point_results"] = effective_points(attempt)
            attempt["point_summary"] = point_summary(attempt["point_results"])
            from ragkb.application.qa_performance import summarize_performance

            for step in attempt["payload"].get("steps", []):
                step["performance_summary"] = summarize_performance(
                    step.get("result", {}).get("coverage_report", {}).get("performance", {})
                )
            if attempt["payload"].get("content_lineage"):
                attempt["payload"]["content_lineage"] = overlay_review(
                    attempt["payload"]["content_lineage"], attempt["point_results"]
                )
        run["attempts"] = attempts
        calls = self.repository.calls(identity)
        run["usage"] = self.summarize_calls(calls, run["calls_reserved"])
        from ragkb.domain.acceptance_repeats import summarize_repeats

        run["repeat_summary"] = summarize_repeats(run)
        return run

    @staticmethod
    def summarize_calls(calls: list[dict[str, Any]], reserved: int) -> dict[str, Any]:
        return {
            "observed_calls": len(calls),
            "input_tokens": sum(
                c["payload"].get("usage", {}).get("prompt_tokens", 0) for c in calls
            ),
            "output_tokens": sum(
                c["payload"].get("usage", {}).get("completion_tokens", 0) for c in calls
            ),
            "unknown_usage_calls": sum(not c["payload"].get("usage") for c in calls)
            + max(0, reserved - len(calls)),
            "known_cost_cny": sum(c["payload"].get("cost_cny") or 0 for c in calls),
            "unpriced_calls": sum(c["payload"].get("cost_cny") is None for c in calls)
            + max(0, reserved - len(calls)),
        }

    @staticmethod
    def case_sources(snapshot: dict[str, Any], case: dict[str, Any], space: str) -> list[Any]:
        spec = AcceptanceCase.model_validate(case["payload"])
        readings = [spec.reading, *(h.reading for h in spec.history)]
        whole_space = any(not r.document_ids for r in readings)
        ids = {
            *spec.required_source_documents,
            *spec.required_retrieved_documents,
            *(identity for reading in readings for identity in reading.document_ids),
            *(s.document_id for p in spec.criteria for s in p.sources),
        }
        return [
            source
            for source in snapshot["sources"]
            if source["document_id"] in ids or (whole_space and source["space_id"] == space)
        ]

    def compare(
        self, subject: RequestPrincipal, space: str, baseline: str, candidate: str
    ) -> dict[str, Any]:
        old, new = self.detail(subject, space, baseline), self.detail(subject, space, candidate)
        if (
            old["payload"].get("kind") == "case_generation"
            or new["payload"].get("kind") == "case_generation"
        ):
            raise ValueError("GENERATION_RUNS_ARE_NOT_QA_COMPARISONS")
        before = {a["case_id"]: a for a in old["attempts"]}
        after = {a["case_id"]: a for a in new["attempts"]}
        old_cases = {c["id"]: c for c in old["payload"]["cases"]}
        rows = []
        for case in new["payload"]["cases"]:
            left, right = before.get(case["id"]), after.get(case["id"])
            comparable = (
                old_cases.get(case["id"]) == case
                and self.case_sources(old["payload"]["snapshot"], case, space)
                == self.case_sources(new["payload"]["snapshot"], case, space)
                and old["payload"]["snapshot"]["scope_hash"]
                == new["payload"]["snapshot"]["scope_hash"]
                and old["payload"]["snapshot"]["evaluation_revision"]
                == new["payload"]["snapshot"]["evaluation_revision"]
                and all(
                    old["payload"]["snapshot"]
                    .get("original_standards", {})
                    .get(p.get("original_standard_id"))
                    == new["payload"]["snapshot"]
                    .get("original_standards", {})
                    .get(p.get("original_standard_id"))
                    for p in case["payload"].get("criteria", [])
                    if p.get("original_standard_id")
                )
                and old["payload"].get("review_mode", "manual")
                == new["payload"].get("review_mode", "manual")
                and (
                    new["payload"].get("review_mode") != "assisted"
                    or old["payload"]["snapshot"]["config"]["verifier_model"]
                    == new["payload"]["snapshot"]["config"]["verifier_model"]
                )
                and not (left or {}).get("payload", {}).get("sources_unavailable")
                and not (right or {}).get("payload", {}).get("sources_unavailable")
            )
            a, b = left["verdict"] if left else "not_run", right["verdict"] if right else "not_run"
            change = "not_comparable" if not comparable else "pending"
            if comparable and a in {"passed", "failed"} and b in {"passed", "failed"}:
                change = "unchanged" if a == b else "improved" if b == "passed" else "regressed"
            point_change = compare_points(left, right) if comparable else None
            if point_change:
                texts = {p["id"]: p["text"] for p in criteria_for(case["payload"])}
                for point in point_change["rows"]:
                    point["text"] = texts.get(point["point_id"], point["point_id"])
            before_seconds = (
                left["payload"].get("qa_elapsed_seconds", left["payload"].get("elapsed_seconds"))
                if left
                else None
            )
            after_seconds = (
                right["payload"].get("qa_elapsed_seconds", right["payload"].get("elapsed_seconds"))
                if right
                else None
            )
            faster = (
                comparable
                and before_seconds is not None
                and after_seconds is not None
                and after_seconds < before_seconds
            )
            timing = (
                "faster_with_quality_loss"
                if faster and (change == "regressed" or (point_change or {}).get("regressed"))
                else "faster_verified"
                if faster and a == b == "passed"
                else "quality_pending"
                if faster
                else "no_speedup"
            )
            rows.append(
                {
                    "case_id": case["id"],
                    "key": case["payload"]["key"],
                    "before": a,
                    "after": b,
                    "change": change,
                    "before_seconds": before_seconds,
                    "after_seconds": after_seconds,
                    "points": point_change,
                    "checks": [
                        {
                            "name": check["name"],
                            "before": prior_check["passed"],
                            "after": check["passed"],
                        }
                        for check in (right or {}).get("payload", {}).get("checks", [])
                        for prior_check in (left or {}).get("payload", {}).get("checks", [])
                        if comparable
                        and prior_check["name"] == check["name"]
                        and prior_check["passed"] != check["passed"]
                    ],
                    "lineage": compare_lineage(
                        (left or {}).get("payload", {}).get("content_lineage", {}),
                        (right or {}).get("payload", {}).get("content_lineage", {}),
                    )
                    if comparable
                    else [],
                    "timing_assessment": timing,
                }
            )
        for case_id in sorted(
            old_cases.keys() - {c["id"] for c in new["payload"]["cases"]},
            key=lambda identity: old_cases[identity]["payload"]["key"],
        ):
            rows.append(
                {
                    "case_id": case_id,
                    "key": old_cases[case_id]["payload"]["key"],
                    "before": before.get(case_id, {}).get("verdict", "not_run"),
                    "after": "not_run",
                    "change": "removed",
                }
            )
        common = {
            row["case_id"]
            for row in rows
            if row["change"] not in {"not_comparable", "removed"}
            and before.get(row["case_id"], {}).get("state") in {"completed", "failed"}
            and after.get(row["case_id"], {}).get("state") in {"completed", "failed"}
        }
        common_usage = []
        for identity, attempts in ((baseline, old["attempts"]), (candidate, new["attempts"])):
            # Recovery can reuse an earlier QA receipt. Its calls still belong to
            # this case's cost, along with failed or interrupted attempts.
            attempt_ids = {attempt["id"] for attempt in attempts if attempt["case_id"] in common}
            calls = [c for c in self.repository.calls(identity) if c["attempt_id"] in attempt_ids]
            common_usage.append(self.summarize_calls(calls, len(calls)))
        from ragkb.domain.acceptance_repeats import compare_repeats

        repeated = compare_repeats(old, new, rows)
        return {
            "repeat_comparison": repeated,
            "baseline": baseline,
            "candidate": candidate,
            "rows": rows,
            "before_usage": old["usage"],
            "after_usage": new["usage"],
            "before_case_count": len(old_cases),
            "after_case_count": len(new["payload"]["cases"]),
            "common_case_count": len(common),
            "common_before_usage": common_usage[0],
            "common_after_usage": common_usage[1],
            "before_snapshot": old["payload"]["snapshot"],
            "after_snapshot": new["payload"]["snapshot"],
        }
