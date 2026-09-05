"""Renew a job lease while parsing blocks; losing it fences subsequent writes."""

from __future__ import annotations

from threading import Event, Thread

from ragkb.contracts.jobs import PersistentJobQueuePort, QueueLeaseError


class LeaseGuard:
    def __init__(self, queue: PersistentJobQueuePort, job_id: str, owner: str, seconds: float):
        self.queue, self.job_id, self.owner, self.seconds = queue, job_id, owner, seconds
        self.stopped = Event()
        self.lost = Event()
        self.cancelled = Event()
        self.thread = Thread(target=self._renew, name="rag-lease-renewal", daemon=True)

    def _renew(self) -> None:
        while not self.stopped.wait(max(0.01, self.seconds / 3)):
            try:
                self.check()
            except Exception:
                self.lost.set()
                return

    def check(self) -> bool:
        if self.lost.is_set():
            raise QueueLeaseError("INGEST_LEASE_LOST")
        try:
            job = self.queue.heartbeat(self.job_id, self.owner, lease_seconds=self.seconds)
        except Exception as error:
            self.lost.set()
            raise QueueLeaseError("INGEST_LEASE_LOST") from error
        if job.cancel_requested:
            self.cancelled.set()
        return self.cancelled.is_set()

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=min(self.seconds, 1.0))
