"""Create a feedback work item inside the original feedback transaction."""

import json
import time
from dataclasses import asdict
from typing import Any

from ragkb.domain.rag import Feedback


def capture(cursor: Any, feedback: Feedback, identity: str, *, mysql: bool = False) -> None:
    if feedback.rating > 3:
        return
    now = time.time()
    payload = {
        "feedback": asdict(feedback),
        "owner_id": "",
        "case_id": "",
        "case_revision": 0,
        "retest_after": now,
        "resolution": {},
        "history": [{"action": "reported", "actor_id": feedback.user_id, "at": now}],
    }
    sql = (
        "INSERT INTO feedback_work_items "
        "(id,tenant_id,space_id,state,revision,payload_json,created_at,updated_at) "
        "VALUES(?,?,?,'open',1,?,?,?) "
        + ("ON DUPLICATE KEY UPDATE id=id" if mysql else "ON CONFLICT(id) DO NOTHING")
    )
    cursor.execute(
        sql.replace("?", "%s") if mysql else sql,
        (
            identity,
            feedback.tenant_id,
            feedback.space_id,
            json.dumps(payload, ensure_ascii=False),
            now,
            now,
        ),
    )
