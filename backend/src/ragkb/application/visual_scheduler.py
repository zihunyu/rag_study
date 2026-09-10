"""Bounded image checks with stable results and request-scoped cancellation."""

from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from threading import Event

from ragkb.application.cancellation import cancellation_scope, check_cancelled
from ragkb.application.qa_performance import record_event, timed_stage


def run_visual_checks[T, R](
    jobs: Sequence[T], run: Callable[[T], R], *, workers: int
) -> tuple[R, ...]:
    if not jobs:
        return ()
    workers = max(1, min(workers, len(jobs)))
    record_event("visual_scheduler", images=len(jobs), workers=workers)
    stop = Event()

    def execute(index: int) -> R:
        with (
            cancellation_scope(stop.is_set),
            timed_stage("evidence.visual_query", image_number=index + 1),
        ):
            check_cancelled()
            result = run(jobs[index])
            check_cancelled()
            return result

    # Keep serial configuration usable with transports that do not support threads.
    if workers == 1:
        return tuple(execute(index) for index in range(len(jobs)))

    results: dict[int, R] = {}
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="image-review")
    pending: dict[Future[R], int] = {}
    next_index = 0
    try:
        while next_index < len(jobs) or pending:
            check_cancelled()
            while next_index < len(jobs) and len(pending) < workers:
                pending[executor.submit(copy_context().run, execute, next_index)] = next_index
                next_index += 1
            done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            # Process every completed future before scheduling more work. A failure
            # must stop queued images even if another image succeeded at the same time.
            for future in sorted(done, key=lambda f: pending[f]):
                index = pending.pop(future)
                results[index] = future.result()
        return tuple(results[index] for index in range(len(jobs)))
    finally:
        stop.set()
        for future in pending:
            future.cancel()
        # In-flight HTTP requests honor their inherited deadline. Do not detach them
        # or return a partial result while they can still spend the request budget.
        executor.shutdown(wait=True, cancel_futures=True)
