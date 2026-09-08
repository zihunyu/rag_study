"""Worker liveness and independent task-progress observations in the shared local ledger."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any

from ragkb.contracts.jobs import QueueJob
from ragkb.infrastructure.visual_ledger import VisualLedger


class WorkerHeartbeat:
    def __init__(self, ledger: VisualLedger, worker_id: str, interval: float = 5) -> None:
        self.ledger, self.worker_id, self.interval = ledger, worker_id, interval
        self.identity = worker_id + ":" + uuid.uuid4().hex
        self.lock = threading.Lock()
        self.publishing = threading.Lock()
        self.stopped = threading.Event()
        self.data: dict[str, Any] = {
            "worker_id": worker_id,
            "phase": "starting",
            "job_id": "",
            "version_id": "",
            "activity_epoch": time.time(),
            "stopped": False,
        }
        self.thread = threading.Thread(target=self._run, name="rag-worker-heartbeat", daemon=True)

    def activity(self, phase: str, job: QueueJob | None = None) -> None:
        with self.lock:
            identity = job.id if job else ""
            if (phase, identity) != (self.data["phase"], self.data["job_id"]):
                self.data.update(
                    phase=phase,
                    job_id=identity,
                    version_id=str(job.payload.get("document_version_id", "")) if job else "",
                    activity_epoch=time.time(),
                )

    def publish(self) -> None:
        with self.publishing:
            with self.lock:
                snapshot = {**self.data, "heartbeat_epoch": time.time()}
            self.ledger.put("worker_heartbeat", self.identity, snapshot)

    def _run(self) -> None:
        while not self.stopped.wait(self.interval):
            try:
                self.publish()
            except (OSError, RuntimeError, sqlite3.Error):
                # A failed write naturally becomes a stale heartbeat; never claim health.
                continue

    def start(self) -> None:
        self.publish()
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        with self.lock:
            self.data.update(stopped=True, phase="stopped")
        self.thread.join(timeout=1)
        self.publish()


def worker_status(
    ledger: VisualLedger,
    *,
    stale_seconds: float = 30,
    stall_seconds: float = 900,
    now: float | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    with ledger.connect() as db:
        rows = db.execute(
            "SELECT payload FROM visual_metadata WHERE namespace='worker_heartbeat' "
            "ORDER BY updated DESC LIMIT 100"
        ).fetchall()
    if not rows:
        return {"state": "unprobed", "reason": "尚未收到文件处理进程的心跳", "instances": []}
    entries = [json.loads(row[0]) for row in rows]
    instances = []
    for item in entries:
        recent = 0 <= now - item.get("heartbeat_epoch", 0) <= stale_seconds
        live = recent and not item.get("stopped")
        last_progress = item.get("activity_epoch", 0)
        if live and item.get("version_id"):
            assets = ledger.assets(item["version_id"])
            last_progress = max(
                [
                    last_progress,
                    *(a.get("updated_at", 0) for a in assets if a.get("updated_at", 0) <= now),
                ]
            )
        stalled = live and bool(item.get("job_id")) and now - last_progress > stall_seconds
        instances.append(
            {
                **item,
                "state": "stalled" if stalled else "ready" if live else "unavailable",
                "progress_age_seconds": max(0, now - last_progress),
            }
        )
    active = [i for i in instances if i["state"] in {"ready", "stalled"}]
    stalled = [i for i in active if i["state"] == "stalled"]
    state = "stalled" if stalled else "ready" if active else "unavailable"
    return {
        "state": state,
        "reason": {
            "ready": f"{len(active)} 个处理进程持续上报心跳，任务进度正常",
            "stalled": f"{len(stalled)} 个任务长时间没有阶段进展，请查看任务中心",
            "unavailable": "处理进程心跳已过期或已停止，请启动 Worker",
        }[state],
        "instances": instances[:20],
        "stale_seconds": stale_seconds,
        "stall_seconds": stall_seconds,
    }
