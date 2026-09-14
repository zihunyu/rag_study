"""Scoped, revisioned feedback triage linked to real acceptance outcomes."""

# ruff: noqa: S608 -- only a fixed FOR UPDATE suffix; values are bound parameters.

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from ragkb.domain.acceptance import AcceptanceCase
from ragkb.domain.uploads import OptimisticConcurrencyError, ResourceNotFoundError
from ragkb.infrastructure.acceptance_repository import decoded, encode

if TYPE_CHECKING:
    from ragkb.infrastructure.acceptance_service import AcceptanceService


class FeedbackWorkflow:
    def __init__(self, acceptance: AcceptanceService) -> None:
        self.acceptance = acceptance
        self.repo = acceptance.repository
        self.db, self.tenant = self.repo.db, self.repo.tenant

    def list(self, space: str, *, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.connection() as c:
            rows = self.db.rows(
                c,
                "SELECT * FROM feedback_work_items WHERE tenant_id=? AND space_id=? "
                "ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?",
                (self.tenant, space, limit, offset),
            )
            return [decoded(r) for r in rows]

    def get(self, c: Any, space: str, identity: str, *, lock: bool = False) -> dict[str, Any]:
        row = self.db.one(
            c,
            "SELECT * FROM feedback_work_items "
            "WHERE tenant_id=? AND space_id=? AND id=?" + (self.repo.lock() if lock else ""),
            (self.tenant, space, identity),
        )
        if not row:
            raise ResourceNotFoundError(identity)
        return decoded(row)

    def detail(self, space: str, identity: str) -> dict[str, Any]:
        with self.db.connection() as c:
            return self.get(c, space, identity)

    def update(
        self,
        space: str,
        identity: str,
        actor: str,
        revision: int,
        action: str,
        note: str,
        *,
        case_id: str = "",
        attempt_id: str = "",
    ) -> dict[str, Any]:
        now = time.time()
        with self.db.transaction() as c:
            item = self.get(c, space, identity, lock=True)
            if item["revision"] != revision:
                raise OptimisticConcurrencyError(identity)
            payload, state = item["payload"], item["state"]
            if state in {"resolved", "dismissed"} and action != "reopen":
                raise ValueError("FEEDBACK_REOPEN_REQUIRED")
            if action == "claim":
                payload["owner_id"], state = actor, "in_progress"
            elif action in {"create_case", "link_case"}:
                if action == "create_case":
                    if payload["case_id"]:
                        raise ValueError("FEEDBACK_ALREADY_HAS_CASE")
                    case = AcceptanceCase(
                        key="feedback-" + identity,
                        question=payload["feedback"]["question"],
                        category="差评回归",
                        source_notes="请对照原始资料填写预期要求并确认；差评和现有回答不是标准答案。\n"
                        + payload["feedback"]["reason_code"]
                        + "："
                        + payload["feedback"]["comment"],
                    )
                    saved = self.repo.save_case(
                        space, actor, case.model_dump(mode="json"), 0, connection=c
                    )
                    case_id = saved["id"]
                case_row = self._case(c, space, case_id)
                if (
                    case_row["payload"]["question"].strip()
                    != payload["feedback"]["question"].strip()
                ):
                    raise ValueError("FEEDBACK_CASE_QUESTION_MISMATCH")
                payload.update(
                    case_id=case_id,
                    case_revision=case_row["revision"],
                    retest_after=now,
                    resolution={},
                )
                state = "in_progress"
            elif action == "ready_for_retest":
                case_row = self._case(c, space, payload["case_id"])
                if (
                    case_row["payload"]["question"].strip()
                    != payload["feedback"]["question"].strip()
                ):
                    raise ValueError("FEEDBACK_CASE_QUESTION_MISMATCH")
                if (
                    case_row["payload"]["review_state"] != "confirmed"
                    or case_row["payload"]["archived"]
                ):
                    raise ValueError("FEEDBACK_CONFIRMED_CASE_REQUIRED")
                payload.update(case_revision=case_row["revision"], retest_after=now, resolution={})
                state = "ready_for_retest"
            elif action == "record_retest":
                if state != "ready_for_retest":
                    raise ValueError("FEEDBACK_NOT_READY_FOR_RETEST")
                resolution = self._retest(c, space, payload, attempt_id)
                payload["resolution"] = resolution
                state = "resolved" if resolution["verdict"] == "passed" else "in_progress"
            elif action == "dismiss":
                state = "dismissed"
            elif action == "reopen":
                payload.update(retest_after=now, resolution={})
                state = "open"
            else:
                raise ValueError("FEEDBACK_ACTION_INVALID")
            payload["history"].append(
                {
                    "action": action,
                    "actor_id": actor,
                    "note": note,
                    "at": now,
                    "state": state,
                    "case_id": payload["case_id"],
                    "case_revision": payload["case_revision"],
                    "resolution": payload["resolution"],
                }
            )
            self.db.execute(
                c,
                "UPDATE feedback_work_items SET state=?,revision=?,payload_json=?,"
                "updated_at=? WHERE id=?",
                (state, revision + 1, encode(payload), now, identity),
            )
            return {
                **item,
                "state": state,
                "revision": revision + 1,
                "updated_at": now,
                "payload": payload,
            }

    def _case(self, c: Any, space: str, identity: str) -> dict[str, Any]:
        row = self.db.one(
            c,
            "SELECT * FROM acceptance_cases WHERE id=? AND tenant_id=? "
            "AND space_id=?" + self.repo.lock(),
            (identity, self.tenant, space),
        )
        if not row:
            raise ResourceNotFoundError(identity)
        return decoded(row)

    def _retest(self, c: Any, space: str, payload: dict[str, Any], identity: str) -> dict[str, Any]:
        row = self.db.one(
            c,
            "SELECT a.*,r.payload_json AS run_payload,r.tenant_id,r.space_id "
            "FROM acceptance_attempts a JOIN acceptance_runs r ON r.id=a.run_id "
            "WHERE a.id=? AND r.tenant_id=? AND r.space_id=?",
            (identity, self.tenant, space),
        )
        if not row or row["case_id"] != payload["case_id"]:
            raise ResourceNotFoundError(identity)
        if (
            row["state"] not in {"completed", "failed"}
            or row["created_at"] < payload["retest_after"]
        ):
            raise ValueError("FEEDBACK_FRESH_COMPLETED_RETEST_REQUIRED")
        case = self._case(c, space, payload["case_id"])
        frozen = next(
            (v for v in json.loads(row["run_payload"])["cases"] if v["id"] == case["id"]), None
        )
        if (
            not frozen
            or frozen["revision"] != case["revision"]
            or case["revision"] != payload["case_revision"]
            or case["payload"]["review_state"] != "confirmed"
            or case["payload"]["archived"]
            or case["payload"]["question"].strip() != payload["feedback"]["question"].strip()
        ):
            raise ValueError("FEEDBACK_RETEST_CASE_CHANGED")
        newest = self.db.one(
            c,
            "SELECT a.id FROM acceptance_attempts a JOIN acceptance_runs r "
            "ON r.id=a.run_id WHERE a.case_id=? AND r.tenant_id=? AND r.space_id=? "
            "ORDER BY a.created_at DESC,a.id DESC LIMIT 1",
            (case["id"], self.tenant, space),
        )
        if not newest or newest["id"] != identity:
            raise ValueError("FEEDBACK_NEWER_RETEST_EXISTS")
        review = self.db.one(
            c,
            "SELECT * FROM acceptance_reviews WHERE attempt_id=? "
            "ORDER BY created_at DESC,id DESC LIMIT 1",
            (identity,),
        )
        if not review or review["verdict"] not in {"passed", "failed"}:
            raise ValueError("FEEDBACK_HUMAN_REVIEW_REQUIRED")
        if row["state"] == "failed" and review["verdict"] == "passed":
            raise ValueError("FEEDBACK_FAILED_RETEST_CANNOT_PASS")
        return {
            "run_id": row["run_id"],
            "attempt_id": identity,
            "review_id": review["id"],
            "verdict": review["verdict"],
            "reviewer_id": review["actor_id"],
            "note": review["note"],
            "case_revision": case["revision"],
            "run_snapshot": json.loads(row["run_payload"]).get("snapshot", {}),
        }
