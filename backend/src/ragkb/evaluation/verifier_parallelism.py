"""Isolated scheduling experiment; production QA does not import this module.

The production verifier owns every prompt, batch boundary, witness check and
retry. This experiment changes only when independent condition batches start.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import copy
from dataclasses import replace

from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
from ragkb.application.deadlines import request_deadline
from ragkb.domain.answer_conditions import condition_requirements
from ragkb.domain.rag import ClaimVerdict, DraftAnswer, Evidence, VerificationResult


def parallel_verify(
    verifier: OpenAICompatibleClaimVerifier,
    question: str,
    draft: DraftAnswer,
    evidence: tuple[Evidence, ...],
    *,
    workers: int = 3,
) -> VerificationResult:
    if not 1 <= workers <= 3:
        raise ValueError("EXPERIMENT_WORKERS_MUST_BE_BETWEEN_ONE_AND_THREE")
    if workers == 1:
        serial = copy(verifier)
        serial._settings = verifier._settings.model_copy(
            update={"verifier_condition_parallelism": 1}
        )
        return serial.verify(question, draft, evidence)
    required = condition_requirements(evidence)
    batches = verifier._condition_batches(required)
    separate_conditions = bool(required) and (
        len(batches) > 1 or len(draft.claims) + len(required) > 16
    )
    with request_deadline(verifier._settings.verifier_total_timeout_seconds):
        base = verifier._verify_claims(
            question, draft, evidence, [] if separate_conditions else required
        )
        if not separate_conditions or not base.supported:
            return base
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="verifier-eval")
        futures = []
        try:
            # Each worker receives the same outer deadline, account operation,
            # cancellation and call-budget hooks, in its own copied context.
            futures = [
                executor.submit(
                    copy_context().run,
                    verifier._verify_condition_batch_resilient,
                    question,
                    draft,
                    evidence,
                    batch,
                    required,
                )
                for batch in batches
            ]
            # Preserve source/batch order regardless of completion order. Nothing
            # is returned unless all batches and their normal validators succeed.
            checks = tuple(check for future in futures for check in future.result())
        finally:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
        missing = tuple(
            ClaimVerdict(
                c["source_quote"],
                (c["evidence_id"],),
                "INSUFFICIENT",
                "ANSWER_KEY_CONDITION_MISSING",
            )
            for c in checks
            if c["status"] == "missing"
        )
        return replace(
            base,
            verdicts=base.verdicts + missing,
            evidence_support_verified=base.evidence_support_verified and not missing,
            condition_checks=checks,
        )
