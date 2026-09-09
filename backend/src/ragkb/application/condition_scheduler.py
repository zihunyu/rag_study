"""Bounded independent batches; the caller owns every semantic check."""

from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from threading import Event

from ragkb.application.cancellation import cancellation_scope, check_cancelled
from ragkb.application.qa_performance import timed_stage


def run_condition_batches[T, R](
    batches: Sequence[T], run: Callable[[T], Sequence[R]], *, workers: int
) -> tuple[R, ...]:
    stop = Event()
    results: dict[int, Sequence[R]] = {}
    completed = 0

    def execute(index: int) -> Sequence[R]:
        # Preserve caller cancellation as well as sibling-failure cancellation.
        def cancelled() -> bool:
            return stop.is_set()

        check_cancelled()
        with (
            cancellation_scope(cancelled),
            timed_stage("verification.conditions", batch_number=index + 1),
        ):
            return run(batches[index])

    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="condition-review")
    pending: dict[Future[Sequence[R]], int] = {}
    next_index = 0
    try:
        while next_index < len(batches) or pending:
            check_cancelled()
            while next_index < len(batches) and len(pending) < workers:
                future = executor.submit(copy_context().run, execute, next_index)
                pending[future] = next_index
                next_index += 1
            done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda f: pending[f]):
                index = pending.pop(future)
                try:
                    results[index] = future.result()
                    completed += 1
                except Exception as error:
                    if hasattr(error, "diagnostic"):
                        error.diagnostic.update(
                            verification_stage="conditions",
                            batch_number=index + 1,
                            batch_count=len(batches),
                            completed_batches=completed,
                        )
                    raise
        return tuple(check for index in range(len(batches)) for check in results[index])
    finally:
        stop.set()
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
