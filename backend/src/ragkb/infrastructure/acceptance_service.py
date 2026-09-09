"""Persistent acceptance execution integrating the normal QA pipeline and adapters."""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any

from ragkb.adapters.auth import AuthorizationError
from ragkb.adapters.conversation_context import bounded_history
from ragkb.application.acceptance_budget import AcceptancePaused, acceptance_budget
from ragkb.application.cancellation import cancellation_scope
from ragkb.application.deadlines import request_deadline
from ragkb.application.reading_scope import ReadingOptions, reading_scope
from ragkb.domain.acceptance import AcceptanceCase, fingerprint, mechanical_checks
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.errors import RAGError
from ragkb.domain.ids import new_uuid7
from ragkb.domain.rag import Evidence
from ragkb.domain.uploads import IdempotencyConflictError, ResourceNotFoundError
from ragkb.infrastructure.acceptance_repository import AcceptanceRepository
from ragkb.infrastructure.conversation_service import ConversationService
from ragkb.infrastructure.model_account import background_requests, provider_operation
from ragkb.runtime_components import RuntimeComponents


class AcceptanceService:
    def __init__(self, runtime: RuntimeComponents, conversation: ConversationService) -> None:
        self.runtime, self.conversation = runtime, conversation
        self.repository = AcceptanceRepository(conversation.queries.db, runtime.tenant_id)
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
        }:
            self.authorize(subject, self.runtime.repository.get_document_space(identity))
        if case.review_state == "confirmed" and not case.source_notes.strip():
            raise ValueError("CONFIRMED_CASE_REQUIRES_SOURCE_NOTES")
        if (
            case.review_state == "confirmed"
            and case.expected_status == "answered"
            and not (case.required_points and case.required_source_documents)
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
        return {
            "sources": sources,
            "config": config,
            "code_revision": self.code_revision,
            "evaluation_revision": "acceptance-v1-human-semantic-review",
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
    ) -> dict[str, Any]:
        self.authorize(subject, space)
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
            space, subject.user_id, key, {"name": name, "cases": cases, "snapshot": snapshot}, limit
        )

    def start(self, subject: RequestPrincipal, space: str, identity: str) -> dict[str, Any]:
        self.authorize(subject, space)
        run = self.repository.run(space, identity)
        run.pop("execution_token", None)
        with self.schedule_lock:
            run["queued"] = run["state"] == "ready" and identity in self.scheduled
        if run["actor_id"] != subject.user_id:
            raise AuthorizationError("ONLY_RUN_CREATOR_CAN_RESUME")
        if run["state"] in {"running", "completed"}:
            return run
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
                a["case_id"]
                for a in repo.attempts(identity)
                if a["state"] in {"completed", "failed"}
            }
            for case in run["payload"]["cases"]:
                if case["id"] in completed:
                    continue
                if self.stopping.is_set() or lost.is_set():
                    raise AcceptancePaused("RUN_PAUSED")
                if self.snapshot(subject, space, run["payload"]["cases"]) != original:
                    raise AcceptancePaused("RUN_SNAPSHOT_CHANGED_CREATE_NEW_RUN")
                attempt = repo.begin_attempt(identity, token, case["id"])
                payload = {"steps": [], "checks": [], "error_code": ""}
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
                    history: list[dict[str, Any]] = []
                    spec = case["payload"]
                    questions = [
                        *spec["history"],
                        {"question": spec["question"], "reading": spec["reading"]},
                    ]
                    for index, question in enumerate(questions):
                        self.authorize(subject, space)
                        reading = ReadingOptions.model_validate(question["reading"])
                        progress: dict[str, Any] = {}
                        with (
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
                                step: dict[str, Any] = {
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
                                    "diagnostics": package.diagnostics if package else {},
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
        run["attempts"] = attempts
        calls = self.repository.calls(identity)
        run["usage"] = self.summarize_calls(calls, run["calls_reserved"])
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
            )
            a, b = left["verdict"] if left else "not_run", right["verdict"] if right else "not_run"
            change = "not_comparable" if not comparable else "pending"
            if comparable and a in {"passed", "failed"} and b in {"passed", "failed"}:
                change = "unchanged" if a == b else "improved" if b == "passed" else "regressed"
            rows.append(
                {
                    "case_id": case["id"],
                    "key": case["payload"]["key"],
                    "before": a,
                    "after": b,
                    "change": change,
                    "before_seconds": left["payload"].get("elapsed_seconds") if left else None,
                    "after_seconds": right["payload"].get("elapsed_seconds") if right else None,
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
        for identity, attempts in ((baseline, before), (candidate, after)):
            attempt_ids = {attempts[case_id]["id"] for case_id in common}
            calls = [c for c in self.repository.calls(identity) if c["attempt_id"] in attempt_ids]
            common_usage.append(self.summarize_calls(calls, len(calls)))
        return {
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
