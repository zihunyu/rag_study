"""Knowledge-base-scoped acceptance APIs for managers and administrators."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ragkb.adapters.auth import AuthorizationError
from ragkb.api.support import principal, require_role
from ragkb.application.reading_scope import ReadingOptions
from ragkb.domain.acceptance import AcceptanceCase, HistoryQuestion
from ragkb.domain.acceptance_points import PointReview, SourceBinding, criteria_for
from ragkb.domain.parsing_acceptance import ParsingStandard
from ragkb.domain.uploads import ResourceNotFoundError
from ragkb.infrastructure.acceptance_service import AcceptanceService


class SaveCase(BaseModel):
    case: AcceptanceCase
    revision: int = Field(default=0, ge=0)


class SaveParsingStandard(BaseModel):
    standard: ParsingStandard
    revision: int = Field(default=0, ge=0)


class RunParsing(BaseModel):
    standard_id: str = Field(min_length=1, max_length=191)
    version_id: str = Field(min_length=1, max_length=191)


class ImportCases(BaseModel):
    cases: list[AcceptanceCase] = Field(min_length=1, max_length=100)


class CreateRun(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    case_ids: list[str] = Field(min_length=1, max_length=100)
    call_limit: int = Field(default=100, ge=1, le=2000)
    review_mode: Literal["manual", "assisted"] = "manual"
    repeat_count: int = Field(default=1, ge=1, le=5)


class GenerateCandidates(BaseModel):
    sources: list[SourceBinding] = Field(min_length=1, max_length=20)
    count: int = Field(default=3, ge=1, le=10)
    call_limit: int = Field(default=3, ge=1, le=30)


class Budget(BaseModel):
    call_limit: int = Field(ge=1, le=2000)


class Review(BaseModel):
    verdict: Literal["passed", "failed"]
    note: str = Field(min_length=1, max_length=4000, pattern=r".*\S.*")
    checked_points: list[int] = Field(default_factory=list, max_length=60)
    sources_checked: bool = False
    point_reviews: list[PointReview] = Field(default_factory=list, max_length=61)


class FromTurn(BaseModel):
    conversation_id: str
    turn_id: str
    key: str = Field(min_length=1, max_length=80)


def build_acceptance_router(service: AcceptanceService) -> APIRouter:
    router = APIRouter(prefix="/api/spaces/{space_id}/acceptance", tags=["acceptance"])
    repo = service.repository

    @router.get("/parsing/standards")
    def parsing_standards(space_id: str, request: Request) -> list[dict[str, Any]]:
        return service.parsing.records(principal(request), space_id, "standard")

    @router.post("/parsing/standards")
    def save_parsing_standard(
        space_id: str, body: SaveParsingStandard, request: Request
    ) -> dict[str, Any]:
        try:
            return service.parsing.save(principal(request), space_id, body.standard, body.revision)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/parsing/standards/{identity}/revisions")
    def parsing_revisions(space_id: str, identity: str, request: Request) -> list[dict[str, Any]]:
        standard = service.parsing.record(principal(request), space_id, identity)
        return [
            service.parsing.record(principal(request), space_id, f"{identity}:{i}")
            for i in range(standard["revision"], 0, -1)
        ]

    @router.get("/parsing/versions/{identity}")
    def parsing_version(
        space_id: str,
        identity: str,
        request: Request,
        pages: str = Query(default="1", max_length=200),
    ) -> dict[str, Any]:
        try:
            selected = {int(p.strip()) for p in pages.split(",")}
            if not selected or len(selected) > 20 or min(selected) < 1 or max(selected) > 100000:
                raise ValueError("SELECT_UP_TO_20_ORIGINAL_PAGES")
            subject = principal(request)
            version = service.parsing.version(subject, space_id, identity)
            return service.parsing.snapshot(subject, space_id, identity, selected) | {
                "mime_type": version["mime_type"],
                "document_id": version["document_id"],
            }
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/parsing/runs")
    def parsing_runs(space_id: str, request: Request) -> list[dict[str, Any]]:
        return service.parsing.records(principal(request), space_id, "run")

    @router.post("/parsing/runs")
    def run_parsing(
        space_id: str,
        body: RunParsing,
        request: Request,
        key: str = Header(alias="Idempotency-Key", min_length=1, max_length=191),
    ) -> dict[str, Any]:
        try:
            return service.parsing.run(
                principal(request), space_id, body.standard_id, body.version_id, key
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/parsing/compare")
    def compare_parsing(
        space_id: str, request: Request, baseline: str, candidate: str
    ) -> dict[str, Any]:
        return service.parsing.compare(principal(request), space_id, baseline, candidate)

    @router.get("/sources/{document_id}")
    def sources(
        space_id: str,
        document_id: str,
        request: Request,
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        subject = principal(request)
        service.authorize(subject, space_id)
        try:
            return service.assistance.source_page(subject, space_id, document_id, offset)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/candidates:runs")
    def generate(
        space_id: str,
        body: GenerateCandidates,
        request: Request,
        key: str = Header(alias="Idempotency-Key", min_length=1, max_length=191),
    ) -> dict[str, Any]:
        try:
            return service.create_generation(
                principal(request),
                space_id,
                [s.model_dump() for s in body.sources],
                body.count,
                key,
                body.call_limit,
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/cases")
    def cases(space_id: str, request: Request) -> list[dict[str, Any]]:
        subject = principal(request)
        service.authorize(subject, space_id)
        visible = []
        for case in repo.cases(space_id):
            try:
                service.validate_case(
                    subject, space_id, AcceptanceCase.model_validate(case["payload"])
                )
                visible.append(case)
            except (AuthorizationError, ResourceNotFoundError):
                continue
        return visible

    @router.post("/cases")
    def create_case(space_id: str, body: SaveCase, request: Request) -> dict[str, Any]:
        subject = principal(request)
        try:
            service.validate_case(subject, space_id, body.case)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return repo.save_case(
            space_id, subject.user_id, body.case.model_dump(mode="json"), body.revision
        )

    @router.put("/cases/{case_id}")
    def update_case(
        space_id: str, case_id: str, body: SaveCase, request: Request
    ) -> dict[str, Any]:
        subject = principal(request)
        try:
            service.validate_case(subject, space_id, body.case)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if case_id not in {c["id"] for c in repo.cases(space_id)}:
            raise ResourceNotFoundError(case_id)
        return repo.save_case(
            space_id, subject.user_id, body.case.model_dump(mode="json"), body.revision, case_id
        )

    @router.get("/cases/{case_id}/revisions")
    def revisions(space_id: str, case_id: str, request: Request) -> list[dict[str, Any]]:
        subject = principal(request)
        service.authorize(subject, space_id)
        history = repo.revisions(space_id, case_id)
        for revision in history:
            service.validate_case(
                subject, space_id, AcceptanceCase.model_validate(revision["payload"])
            )
        return history

    @router.post("/cases:import")
    def import_cases(space_id: str, body: ImportCases, request: Request) -> dict[str, Any]:
        subject = principal(request)
        service.authorize(subject, space_id)
        # Imports are candidates, even if an external file claims prior approval.
        imported, skipped = [], []
        known = {c["case_key"] for c in repo.cases(space_id)}
        for case in body.cases:
            case.review_state = "candidate"
            try:
                service.validate_case(subject, space_id, case)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
        for case in body.cases:
            if case.key in known:
                skipped.append(case.key)
                continue
            imported.append(
                repo.save_case(space_id, subject.user_id, case.model_dump(mode="json"), 0)
            )
            known.add(case.key)
        return {"imported": imported, "skipped_existing": skipped}

    @router.post("/cases:from-turn")
    def from_turn(space_id: str, body: FromTurn, request: Request) -> dict[str, Any]:
        subject = principal(request)
        service.authorize(subject, space_id)
        conversations = service.conversation.repository
        conversation = conversations.get(body.conversation_id, subject)
        turn = conversations.turn(body.turn_id)
        if conversation["space_id"] != space_id or turn["conversation_id"] != body.conversation_id:
            raise ResourceNotFoundError(body.turn_id)
        presented = service.conversation.present(turn, conversation, subject)
        result = presented.get("result") or {}
        case = AcceptanceCase(
            key=body.key,
            question=turn["original_question"],
            reading=ReadingOptions.model_validate(presented.get("reading") or {}),
            history=[
                HistoryQuestion(
                    question=h["original_question"],
                    reading=ReadingOptions.model_validate(
                        conversations.turn(h["id"]).get("reading") or {}
                    ),
                )
                for h in conversations.turns(
                    body.conversation_id, before=turn["sequence_number"], limit=6
                )
            ],
            required_source_documents=list(
                {c["document_id"] for c in result.get("citations", []) if c.get("document_id")}
            ),
            source_notes="来自会话 "
            + body.conversation_id
            + "，运行 "
            + str(turn.get("rag_run_id") or "")
            + "。请对照原文补充预期要求，当前回答不会自动成为标准答案。",
        )
        return repo.save_case(space_id, subject.user_id, case.model_dump(mode="json"), 0)

    @router.get("/runs")
    def runs(space_id: str, request: Request) -> list[dict[str, Any]]:
        service.authorize(principal(request), space_id)
        return [
            {k: v for k, v in r.items() if k not in {"payload", "execution_token", "request_key"}}
            | {
                "name": r["payload"]["name"],
                "case_count": len(r["payload"]["cases"]),
                "repeat_count": r["payload"].get("repeat_count", 1),
                "kind": r["payload"].get("kind", "qa"),
            }
            for r in repo.runs(space_id)
        ]

    @router.post("/runs")
    def create_run(
        space_id: str,
        body: CreateRun,
        request: Request,
        key: str = Header(alias="Idempotency-Key", min_length=1, max_length=191),
    ) -> dict[str, Any]:
        try:
            return service.create_run(
                principal(request),
                space_id,
                body.case_ids,
                key,
                body.name,
                body.call_limit,
                body.review_mode,
                body.repeat_count,
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/runs/{run_id}")
    def detail(space_id: str, run_id: str, request: Request) -> dict[str, Any]:
        return service.detail(principal(request), space_id, run_id)

    @router.post("/runs/{run_id}:resume")
    def resume(space_id: str, run_id: str, request: Request) -> dict[str, Any]:
        return service.start(principal(request), space_id, run_id)

    @router.post("/runs/{run_id}:pause")
    def pause(space_id: str, run_id: str, request: Request) -> dict[str, Any]:
        service.authorize(principal(request), space_id)
        repo.pause(space_id, run_id)
        return {"pause_requested": True}

    @router.post("/runs/{run_id}:budget")
    def budget(space_id: str, run_id: str, body: Budget, request: Request) -> dict[str, Any]:
        service.authorize(principal(request), space_id)
        repo.increase_budget(space_id, run_id, body.call_limit)
        return {"call_limit": repo.run(space_id, run_id)["call_limit"]}

    @router.post("/runs/{run_id}/attempts/{attempt_id}/reviews")
    def review(
        space_id: str, run_id: str, attempt_id: str, body: Review, request: Request
    ) -> dict[str, Any]:
        subject = principal(request)
        run = service.detail(subject, space_id, run_id, attempt_id=attempt_id)
        attempt = next((a for a in run["attempts"] if a["id"] == attempt_id), None)
        if not attempt:
            raise ResourceNotFoundError(attempt_id)
        case = next(c["payload"] for c in run["payload"]["cases"] if c["id"] == attempt["case_id"])
        points = criteria_for(case)
        count = len(points)
        structured = [p.model_dump() for p in body.point_reviews]
        if structured:
            if len(structured) != count or {p["point_id"] for p in structured} != {
                p["id"] for p in points
            }:
                raise HTTPException(422, "REVIEW_EVERY_POINT_ONCE")
            answer = (attempt["payload"].get("steps") or [{}])[-1].get("result", {}).get(
                "answer"
            ) or ""
            kinds = {p["id"]: p["kind"] for p in points}
            for point in structured:
                quote = point["answer_quote"]
                if (
                    (quote and quote not in answer)
                    or (
                        point["status"] == "covered"
                        and kinds[point["point_id"]] not in {"forbidden", "relevance"}
                        and not quote.strip()
                    )
                    or (
                        kinds[point["point_id"]] == "relevance"
                        and point["status"] == "incorrect"
                        and not quote.strip()
                    )
                ):
                    raise HTTPException(422, "REVIEW_ANSWER_QUOTE_REQUIRED")
        if (case.get("criteria") or case.get("check_relevance")) and not structured:
            raise HTTPException(422, "REVIEW_EVERY_POINT_ONCE")
        if body.verdict == "passed" and (
            not body.sources_checked
            or (
                any(p["status"] != "covered" for p in structured)
                if structured
                else set(body.checked_points) != set(range(count))
            )
            or attempt["payload"].get("sources_unavailable", False)
            or any(
                s["result"]["status"] == "sources_unavailable"
                for s in attempt["payload"].get("steps", [])
            )
        ):
            raise HTTPException(422, "REVIEW_ALL_POINTS_AND_SOURCES")
        record = repo.review(
            run_id, attempt_id, subject.user_id, body.verdict, body.note, structured or None
        )
        return {"reviewed": True, "review": record}

    @router.get("/runs/{run_id}/attempts/{attempt_id}/diagnostics")
    def diagnostics(
        space_id: str, run_id: str, attempt_id: str, request: Request
    ) -> dict[str, Any]:
        subject = principal(request)
        require_role(subject, "admin")
        run = service.detail(subject, space_id, run_id, attempt_id=attempt_id)
        attempt = next((a for a in run["attempts"] if a["id"] == attempt_id), None)
        if not attempt or any(
            s["result"]["status"] == "sources_unavailable"
            for s in attempt["payload"].get("steps", [])
        ):
            raise ResourceNotFoundError(attempt_id)
        raw = next(a for a in repo.attempts(run_id) if a["id"] == attempt_id)
        return {"steps": [s.get("diagnostics", {}) for s in raw["payload"].get("steps", [])]}

    @router.get("/compare")
    def compare(space_id: str, baseline: str, candidate: str, request: Request) -> dict[str, Any]:
        try:
            return service.compare(principal(request), space_id, baseline, candidate)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router
