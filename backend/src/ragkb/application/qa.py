"""Trusted QA orchestration with buffered generation and final fail-closed verification."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict, replace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ragkb.contracts.rag import ExactAnswerReusePort
from urllib.parse import urlparse

from ragkb.application.provider_budget import ConservativeTokenCounter
from ragkb.application.qa_diagnostics import (
    diagnostic_scope,
    failure_diagnostics,
    record_failure,
)
from ragkb.application.tracing import InMemoryTracer, TracerPort
from ragkb.contracts.rag import (
    BufferedGenerationPort,
    CitationReferencePort,
    CitationRepairPort,
    ClaimVerifierPort,
    EvidenceProviderPort,
    FinalPermissionPort,
    RAGRunRepositoryPort,
    VerifiedAnswerCachePort,
)
from ragkb.domain.answer_conditions import condition_report
from ragkb.domain.citation_repair import (
    append_missing_text_conditions,
    rebuild_from_supported_claims,
    repairable_citations,
    repairable_surface,
    validate_citation_only_change,
)
from ragkb.domain.claim_coverage import render_verified_claims, verify_answer_claim_coverage
from ragkb.domain.errors import (
    InvalidProviderResponse,
    ProviderRateLimited,
    ProviderTimeout,
    QuestionAssessmentFailed,
    RetrievalFailClosed,
    TransientProviderError,
)
from ragkb.domain.ids import new_uuid7
from ragkb.domain.numeric_facts import (
    check_numeric_facts,
    explicit_calculation_requires_review,
    normalize_numeric_text,
)
from ragkb.domain.policy_conflicts import conflicting_sources
from ragkb.domain.rag import (
    AnswerStatus,
    AskResult,
    Citation,
    ClaimVerdict,
    DraftAnswer,
    DraftAnswerStatus,
    Evidence,
    EvidencePackage,
    Feedback,
    QuestionDisposition,
    VerificationResult,
)
from ragkb.domain.retrieval import RetrievalHealth, SecurityWatermarkNotReady
from ragkb.domain.source_lists import REVISION as SOURCE_LIST_REVISION
from ragkb.domain.source_lists import source_list_plan
from ragkb.domain.visual_claims import used_visual_fact_ids, visual_claim_evidence

_URL_PATTERN = re.compile(r"https?://[^\s)\]}>]+", re.IGNORECASE)
_CREDENTIAL_REQUEST_PATTERN = re.compile(
    r"(?:输入|提供|发送|告知|索取).{0,12}(?:密码|验证码|口令)|"
    r"(?:provide|send|enter|share|ask for).{0,20}(?:password|verification code|credential)",
    re.IGNORECASE,
)
_NUMERIC_REVIEW_REQUIRED = "NUMERIC_FACT_REQUIRES_SEMANTIC_REVIEW"
_SURFACE_REVIEW_REQUIRED = "ANSWER_SURFACE_REQUIRES_SEMANTIC_REVIEW"


def condition_repair_evidence(
    package: EvidencePackage,
    checks: tuple[dict[str, str], ...],
    *,
    cited_ids: tuple[str, ...],
    max_tokens: int = 8000,
) -> tuple[Evidence, ...]:
    """Promote original, authorized condition sources from the review pool within budget.

    Already covered conditions remain mandatory alongside missing conditions: a
    repair must not fix one omission by dropping an earlier, correct qualification.
    Their sources and already cited sources are indivisible.
    """
    applicable = [c for c in checks if c["status"] in {"missing", "covered"}]
    by_id = {e.evidence_id: e for e in package.evidence}
    quotes: dict[str, list[str]] = {}
    for check in applicable:
        source = by_id.get(check["evidence_id"])
        quote = check["source_quote"]
        if (
            source is None
            or not source.authorized
            or not source.current_version
            or not source.valid_at(package.query_time_epoch)
            or not quote.strip()
            or quote not in source.text
        ):
            raise InvalidProviderResponse("VERIFIER_CONDITION_REPAIR_SOURCE_INVALID")
        quotes.setdefault(source.evidence_id, []).append(quote)
    mandatory = set(quotes) | set(cited_ids)
    ordered = [e for e in package.evidence if e.evidence_id in mandatory]
    ordered += [e for e in package.generation_evidence if e.evidence_id not in mandatory]
    counter, used = ConservativeTokenCounter(), 0
    result = []
    for source in ordered:
        item = replace(
            source,
            source_role="hit" if source.source_role == "conflict_context" else source.source_role,
            locator={
                **source.locator,
                "conditions_to_preserve": quotes.get(source.evidence_id, []),
            },
        )
        size = counter.count(
            json.dumps({"text": item.text, "locator": item.locator}, ensure_ascii=False)
        )
        if used + size > max_tokens:
            if source.evidence_id in mandatory:
                raise InvalidProviderResponse(
                    "VERIFIER_CONDITION_REPAIR_BUDGET_EXCEEDED",
                    diagnostic={
                        "verification_stage": "condition_repair",
                        "reason": "condition_repair_sources_exceed_budget",
                        "evidence_id": source.evidence_id,
                        "required_tokens": used + size,
                        "token_limit": max_tokens,
                    },
                )
            continue
        result.append(item)
        used += size
    return tuple(result)


def _normalized_fact_text(value: str) -> str:
    return normalize_numeric_text(value)


class DeterministicClaimVerifier:
    """Fail-closed structural checks that run before any answer is marked verified."""

    revision = "deterministic-claim-verifier:surface-numeric-and-graph-facts-v4-calculations"

    def __init__(self, allowed_output_domains: tuple[str, ...] = ()) -> None:
        self.allowed_output_domains = frozenset(
            domain.casefold().strip(".") for domain in allowed_output_domains if domain.strip()
        )

    def verify(
        self,
        question: str,
        draft: DraftAnswer,
        evidence: tuple[Evidence, ...],
    ) -> VerificationResult:
        del question
        available = {item.evidence_id: item for item in evidence}
        claims = draft.claims
        if not claims:
            return VerificationResult(
                (
                    ClaimVerdict(
                        draft.text,
                        (),
                        "INSUFFICIENT",
                        "ANSWER_CLAIMS_REQUIRED",
                    ),
                ),
                self.revision,
                answer_claims_covered=False,
            )
        coverage = verify_answer_claim_coverage(draft.text, claims)
        if not coverage.complete and not draft.synthesized:
            return VerificationResult(
                tuple(
                    ClaimVerdict(
                        clause,
                        (),
                        "INSUFFICIENT",
                        "ANSWER_CLAIM_UNCOVERED",
                    )
                    for clause in coverage.uncovered_clauses
                ),
                self.revision,
                answer_claims_covered=False,
            )
        declared_citations = set(draft.citation_ids)
        if any(
            evidence_id not in declared_citations
            for claim in claims
            for evidence_id in claim.evidence_ids
        ):
            return VerificationResult(
                (
                    ClaimVerdict(
                        draft.text,
                        (),
                        "INSUFFICIENT",
                        "CLAIM_CITATION_NOT_DECLARED",
                    ),
                ),
                self.revision,
                citation_ids_valid=False,
            )
        verdicts: list[ClaimVerdict] = []
        if draft.synthesized:
            # A summary is not established by string matching atomic claims.
            # Inspect its complete surface before deferring semantic coverage.
            inline_ids = re.findall(r"\[(E[^\]\s]*)\]", draft.text)
            used_ids = {identity for claim in claims for identity in claim.evidence_ids}
            invalid_inline = not inline_ids or any(i not in used_ids for i in inline_ids)
            source_text = "\n".join(available[i].text for i in used_ids if i in available)
            surface_reason = None
            if invalid_inline:
                surface_reason = "ANSWER_INLINE_CITATION_INVALID"
            elif _CREDENTIAL_REQUEST_PATTERN.search(draft.text):
                surface_reason = "UNSUPPORTED_CREDENTIAL_REQUEST"
            elif any(url not in source_text for url in _URL_PATTERN.findall(draft.text)):
                surface_reason = "UNSUPPORTED_EXTERNAL_URL"
            elif any(
                (urlparse(url).hostname or "").casefold().strip(".")
                not in self.allowed_output_domains
                for url in _URL_PATTERN.findall(draft.text)
            ):
                surface_reason = "OUTPUT_URL_DOMAIN_NOT_ALLOWED"
            verdicts.append(
                ClaimVerdict(
                    draft.text, (), "INSUFFICIENT", surface_reason or _SURFACE_REVIEW_REQUIRED
                )
            )
        for claim in claims:
            cited = [available.get(evidence_id) for evidence_id in claim.evidence_ids]
            if not cited or any(item is None for item in cited):
                verdicts.append(
                    ClaimVerdict(
                        claim.text,
                        claim.evidence_ids,
                        "INSUFFICIENT",
                        "CLAIM_EVIDENCE_INVALID",
                    )
                )
                continue
            try:
                cited_evidence = visual_claim_evidence(claim, evidence)
            except ValueError as error:
                verdicts.append(
                    ClaimVerdict(claim.text, claim.evidence_ids, "INSUFFICIENT", str(error))
                )
                continue
            source = "\n".join(item.text for item in cited_evidence)
            numeric_check = check_numeric_facts(claim.text, tuple(e.text for e in cited_evidence))
            unsupported_url = next(
                (url for url in _URL_PATTERN.findall(claim.text) if url not in source), None
            )
            disallowed_url = next(
                (
                    url
                    for url in _URL_PATTERN.findall(claim.text)
                    if (urlparse(url).hostname or "").casefold().strip(".")
                    not in self.allowed_output_domains
                ),
                None,
            )
            asks_for_credentials = bool(_CREDENTIAL_REQUEST_PATTERN.search(claim.text))
            verdict: Literal["SUPPORTED", "CONTRADICTED", "INSUFFICIENT"]
            if unsupported_url is not None:
                verdict, reason = "INSUFFICIENT", "UNSUPPORTED_EXTERNAL_URL"
            elif disallowed_url is not None:
                verdict, reason = "INSUFFICIENT", "OUTPUT_URL_DOMAIN_NOT_ALLOWED"
            elif asks_for_credentials:
                verdict, reason = "INSUFFICIENT", "UNSUPPORTED_CREDENTIAL_REQUEST"
            elif numeric_check == "mismatch" and explicit_calculation_requires_review(
                claim.text, tuple(e.text for e in cited_evidence)
            ):
                verdict, reason = "INSUFFICIENT", _NUMERIC_REVIEW_REQUIRED
            elif numeric_check == "mismatch":
                verdict, reason = "CONTRADICTED", "EXACT_FACT_NOT_IN_EVIDENCE"
            elif numeric_check == "uncertain":
                verdict, reason = "INSUFFICIENT", _NUMERIC_REVIEW_REQUIRED
            else:
                verdict, reason = "SUPPORTED", "STRUCTURE_AND_EXACT_FACTS_SUPPORTED"
            verdicts.append(ClaimVerdict(claim.text, claim.evidence_ids, verdict, reason))
        return VerificationResult(
            tuple(verdicts),
            self.revision,
            answer_claims_covered=not draft.synthesized,
            evidence_support_verified=all(item.verdict == "SUPPORTED" for item in verdicts),
            conflicting_evidence_ids=conflicting_sources(
                evidence, at_epoch=int(time.time()), claims=claims
            ),
        )


class CompositeClaimVerifier:
    """Run deterministic policy checks before the independently configured model verifier."""

    def __init__(self, structural: ClaimVerifierPort, semantic: ClaimVerifierPort) -> None:
        self.structural = structural
        self.semantic = semantic
        self.revision = (
            f"claim-verifier-chain:v2-conflict-on-numeric-rejection:"
            f"{structural.revision}+{semantic.revision}"
        )

    def verify(
        self, question: str, draft: DraftAnswer, evidence: tuple[Evidence, ...]
    ) -> VerificationResult:
        structural = self.structural.verify(question, draft, evidence)
        surface_review = (
            draft.synthesized
            and not structural.answer_claims_covered
            and any(v.reason_code == _SURFACE_REVIEW_REQUIRED for v in structural.verdicts)
        )
        review_reasons = {_NUMERIC_REVIEW_REQUIRED}
        if surface_review:
            review_reasons.add(_SURFACE_REVIEW_REQUIRED)
        semantic_review = (
            bool(structural.verdicts)
            and structural.citation_ids_valid
            and (structural.answer_claims_covered or surface_review)
            and structural.conflict_checked
            and structural.policy_checked
            and not structural.conflicting_evidence_ids
            and any(v.reason_code in review_reasons for v in structural.verdicts)
            and all(
                v.verdict == "SUPPORTED"
                or (v.verdict == "INSUFFICIENT" and v.reason_code in review_reasons)
                for v in structural.verdicts
            )
        )
        if not structural.supported and not semantic_review:
            # A numeric rejection of a comparison must not hide a cross-source
            # conflict. Only retrieve the conflict finding: even a semantic pass
            # cannot override the original hard factual rejection.
            conflict_review = (
                structural.citation_ids_valid
                and structural.policy_checked
                and structural.conflict_checked
                and not structural.conflicting_evidence_ids
                and len({e.document_id for e in evidence}) > 1
                and any(v.reason_code == "EXACT_FACT_NOT_IN_EVIDENCE" for v in structural.verdicts)
                and all(
                    v.verdict == "SUPPORTED"
                    or v.reason_code in {*review_reasons, "EXACT_FACT_NOT_IN_EVIDENCE"}
                    for v in structural.verdicts
                )
            )
            if conflict_review:
                semantic = self.semantic.verify(question, draft, evidence)
                checked = semantic.conflict_checked and semantic.policy_checked
                return replace(
                    structural,
                    revision=self.revision,
                    conflict_checked=checked,
                    conflicting_evidence_ids=semantic.conflicting_evidence_ids if checked else (),
                )
            return VerificationResult(
                structural.verdicts,
                self.revision,
                citation_ids_valid=structural.citation_ids_valid,
                answer_claims_covered=structural.answer_claims_covered,
                evidence_support_verified=structural.evidence_support_verified,
                conflict_checked=structural.conflict_checked,
                policy_checked=structural.policy_checked,
                conflicting_evidence_ids=structural.conflicting_evidence_ids,
            )
        semantic = self.semantic.verify(question, draft, evidence)
        return VerificationResult(
            semantic.verdicts,
            self.revision,
            citation_ids_valid=structural.citation_ids_valid and semantic.citation_ids_valid,
            answer_claims_covered=(
                (structural.answer_claims_covered or surface_review)
                and semantic.answer_claims_covered
            ),
            evidence_support_verified=(
                (structural.evidence_support_verified or semantic_review)
                and semantic.evidence_support_verified
            ),
            conflict_checked=structural.conflict_checked and semantic.conflict_checked,
            policy_checked=structural.policy_checked and semantic.policy_checked,
            conflicting_evidence_ids=semantic.conflicting_evidence_ids,
            condition_checks=semantic.condition_checks,
        )


class TrustedQAService:
    revision = "trusted-qa"

    def __init__(
        self,
        evidence_provider: EvidenceProviderPort,
        generator: BufferedGenerationPort,
        permission: FinalPermissionPort,
        references: CitationReferencePort,
        repository: RAGRunRepositoryPort,
        cache: VerifiedAnswerCachePort | None = None,
        tracer: TracerPort | None = None,
        verifier: ClaimVerifierPort | None = None,
        response_release_guard: Callable[[], AbstractContextManager[object]] | None = None,
    ) -> None:
        self.evidence_provider = evidence_provider
        self.generator = generator
        self.permission = permission
        self.references = references
        self.repository = repository
        self.verifier = verifier or DeterministicClaimVerifier()
        self.cache = cache
        self.result_reuse: ExactAnswerReusePort | None = None
        self.tracer = tracer or InMemoryTracer()
        self.response_release_guard = response_release_guard or nullcontext

    def _save(
        self,
        package: EvidencePackage,
        status: AnswerStatus,
        *,
        answer: str | None = None,
        citations: tuple[Citation, ...] = (),
        warnings: tuple[str, ...] = (),
        verified: bool = False,
        retryable: bool = False,
        condition_checks: tuple[dict[str, str], ...] = (),
    ) -> AskResult:
        if status in {AnswerStatus.SYSTEM_ERROR, AnswerStatus.CONFLICTING_EVIDENCE} or (
            status is AnswerStatus.INSUFFICIENT_EVIDENCE and not verified
        ):
            if not failure_diagnostics():
                record_failure("answer_validation", warnings[0] if warnings else status.value)
            package = replace(package, diagnostics=failure_diagnostics())
        report = dict(package.coverage_report)
        from ragkb.application.qa_performance import performance_report

        performance = performance_report()
        if performance:
            package = replace(
                package, diagnostics={**package.diagnostics, "performance": performance}
            )
            report["performance"] = performance
            model_calls = [
                e for e in performance.get("events", []) if e.get("kind") == "model_http"
            ]
            if (
                status is AnswerStatus.SYSTEM_ERROR
                and model_calls
                and model_calls[-1].get("outcome") == "429"
            ):
                warnings = (*warnings, "MODEL_PROVIDER_RATE_LIMITED")
        failure = package.diagnostics.get("failure", {})
        if failure.get("stage") == "verification" and status is AnswerStatus.SYSTEM_ERROR:
            detail = failure.get("detail", {})
            summary = {
                key: detail[key]
                for key in (
                    "batch_number",
                    "batch_count",
                    "completed_batches",
                    "condition_count",
                    "http_status",
                )
                if type(detail.get(key)) is int
            }
            if detail.get("verification_stage") in {
                "claims_and_conflicts",
                "conditions",
                "condition_repair",
            }:
                summary["stage"] = detail["verification_stage"]
            if detail.get("provider_code") in {
                "MODEL_ACCOUNT_QUOTA_WAIT_TIMEOUT",
                "MODEL_PROVIDER_DEADLINE_EXCEEDED",
                "MODEL_PROVIDER_TIMEOUT",
            }:
                summary["provider_code"] = detail["provider_code"]
            # Only bounded operational metadata is public; drafts, source snippets,
            # model messages and free-form failure explanations remain private.
            if summary:
                report["verification_failure"] = summary
            if failure.get("code") == "MODEL_PROVIDER_RATE_LIMITED":
                report["verification_failure"] = {
                    **summary,
                    "http_status": 429,
                    "provider_code": "MODEL_PROVIDER_RATE_LIMITED",
                }
        if condition_checks:
            report["conditions"] = condition_report(condition_checks)
            if not report["conditions"]["complete"]:
                report["complete"] = False
                report["gaps"] = list(report.get("gaps", [])) + [
                    "答案未完整保留资料中的关键条件，未展示该答案"
                ]
        if answer and report.get("mode") == "overview":
            cited_ids = {c.evidence_id for c in citations}
            cited_sections = {
                (e.document_version_id, str(e.locator.get("section_path", "")))
                for e in package.evidence
                if e.evidence_id in cited_ids
            }
            uncited = [
                {"version_id": s["version_id"], "section": s["section"]}
                for s in report.get("sections", [])
                if (s["version_id"], s["section"]) not in cited_sections
            ]
            report["answer_sections"] = len(cited_sections)
            # Reading coverage measures visited/usable source content. A title
            # or a chapter outside this question need not be cited. Preserve the
            # citation inventory separately; claim and condition verification
            # remain responsible for the answer's factual completeness.
            report["uncited_sections"] = uncited
        result = AskResult(
            rag_run_id=package.rag_run_id,
            status=status,
            answer=answer,
            citations=citations,
            evidence=package.evidence,
            warnings=tuple(dict.fromkeys((*package.retrieval_warnings, *warnings))),
            verified=verified,
            real_acceptance=package.real_acceptance and verified,
            retrieval_health=package.retrieval_health,
            degraded=package.retrieval_health is not RetrievalHealth.HEALTHY,
            retryable=retryable,
            clarification_fields=(
                package.clarification_fields if status is AnswerStatus.NEEDS_CLARIFICATION else ()
            ),
            clarification_question=package.clarification_question,
            coverage="partial"
            if package.coverage == "complete" and report.get("complete") is False
            else package.coverage,
            coverage_report=report,
        )
        self.repository.save_run(package, result)
        return result

    def _permission_recheck(
        self,
        package: EvidencePackage,
        subject_scope_tokens: tuple[str, ...],
        clearance_level: int = 0,
    ) -> bool:
        return self.permission.recheck(
            package.evidence,
            tenant_id=package.tenant_id,
            user_id=package.user_id,
            subject_scope_tokens=subject_scope_tokens,
            permission_revision=package.permission_revision,
            at_epoch=int(time.time()),
            clearance_level=clearance_level,
            generation_id=package.index_generation_id,
        )

    def _ask(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        *,
        subject_scope_tokens: tuple[str, ...] = (),
        clearance_level: int = 0,
        space_id: str | None = None,
    ) -> AskResult:
        try:
            with self.tracer.span("rag.ask.evidence.build"):
                if space_id is None:
                    package = self.evidence_provider.build_package(
                        question,
                        tenant_id,
                        user_id,
                        subject_scope_tokens=subject_scope_tokens,
                        clearance_level=clearance_level,
                    )
                else:
                    package = self.evidence_provider.build_package(
                        question,
                        tenant_id,
                        user_id,
                        subject_scope_tokens=subject_scope_tokens,
                        clearance_level=clearance_level,
                        space_id=space_id,
                    )
            package = replace(
                package,
                subject_authorization_revision="|".join(
                    t for t in subject_scope_tokens if t.startswith("auth-revision:")
                ),
            )
        except (RetrievalFailClosed, TransientProviderError, QuestionAssessmentFailed) as error:
            package = EvidencePackage(
                rag_run_id=new_uuid7(),
                tenant_id=tenant_id,
                user_id=user_id,
                query=question,
                query_time_epoch=int(time.time()),
                index_generation_id="unavailable",
                retrieval_revision=self.evidence_provider.revision,
                prompt_revision=self.generator.revision,
                model_revision=self.generator.revision,
                permission_revision=0,
                evidence=(),
                verifier_revision=self.verifier.revision,
                real_acceptance=False,
                retrieval_health=(
                    RetrievalHealth.HEALTHY
                    if isinstance(error, QuestionAssessmentFailed)
                    else RetrievalHealth.UNAVAILABLE
                ),
            )
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=(
                    error.code
                    if isinstance(error, QuestionAssessmentFailed)
                    else "RETRIEVAL_OR_PERMISSION_FAIL_CLOSED",
                ),
                retryable=(
                    error.retryable
                    if isinstance(error, QuestionAssessmentFailed)
                    else isinstance(error, (TransientProviderError, SecurityWatermarkNotReady))
                ),
            )
        if package.retrieval_health is RetrievalHealth.UNAVAILABLE:
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("RETRIEVAL_UNAVAILABLE",),
                retryable=True,
            )
        if package.disposition is QuestionDisposition.OUT_OF_SCOPE:
            return self._save(
                package,
                AnswerStatus.OUT_OF_SCOPE,
                verified=True,
                warnings=(package.disposition_reason,) if package.disposition_reason else (),
            )
        if package.disposition is QuestionDisposition.NEEDS_CLARIFICATION:
            return self._save(
                package,
                AnswerStatus.NEEDS_CLARIFICATION,
                verified=True,
                warnings=(package.disposition_reason,) if package.disposition_reason else (),
            )
        if not package.generation_evidence:
            if package.retrieval_health is RetrievalHealth.DEGRADED:
                return self._save(
                    package,
                    AnswerStatus.SYSTEM_ERROR,
                    warnings=("RETRIEVAL_INCOMPLETE",),
                    retryable=True,
                )
            return self._save(package, AnswerStatus.INSUFFICIENT_EVIDENCE, verified=True)
        if not all(
            evidence.authorized
            and evidence.current_version
            and evidence.valid_at(package.query_time_epoch)
            and evidence.locator
            for evidence in package.evidence
        ):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("EVIDENCE_VALIDATION_FAILED",),
            )
        if not self._permission_recheck(package, subject_scope_tokens, clearance_level):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("PRE_GENERATION_PERMISSION_RECHECK_FAILED",),
            )
        if package.conflict_detected:
            with self.response_release_guard():
                if not self._permission_recheck(package, subject_scope_tokens, clearance_level):
                    return self._save(
                        package,
                        AnswerStatus.SYSTEM_ERROR,
                        warnings=("FINAL_PERMISSION_RECHECK_FAILED",),
                    )
                return self._save(
                    package,
                    AnswerStatus.CONFLICTING_EVIDENCE,
                    warnings=("UNRESOLVED_POLICY_CONFLICT",),
                    verified=True,
                )
        from ragkb.application.acceptance_budget import fresh_answer_required

        list_plan = source_list_plan(question, package.evidence)
        if list_plan is not None:
            package = replace(
                package,
                model_revision=SOURCE_LIST_REVISION,
                prompt_revision=SOURCE_LIST_REVISION,
                evidence=tuple(
                    replace(e, source_role="hit")
                    if e.evidence_id in list_plan.draft.citation_ids
                    else e
                    for e in package.evidence
                ),
                coverage_report={
                    **package.coverage_report,
                    "source_list": {
                        "revision": SOURCE_LIST_REVISION,
                        "expected_items": len(list_plan.draft.claims),
                    },
                },
            )
        use_cache = self.cache is not None and not fresh_answer_required()
        draft = self.cache.get(package) if self.cache is not None and use_cache else None
        try:
            if draft is None:
                if list_plan is not None:
                    with self.tracer.span("rag.ask.source_list.compose"):
                        draft = list_plan.draft
                else:
                    with self.tracer.span("rag.ask.llm.generate"):
                        draft = self.generator.generate(question, package.generation_evidence)
        except InvalidProviderResponse as error:
            record_failure("generation", error.code, error.diagnostic)
            return self._save(
                package, AnswerStatus.SYSTEM_ERROR, warnings=("GENERATION_PROTOCOL_INVALID",)
            )
        except TransientProviderError as error:
            record_failure("generation", error.code)
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("GENERATION_UNAVAILABLE_AUTHORIZED_EVIDENCE_ONLY",),
                retryable=True,
            )
        if draft.status is DraftAnswerStatus.INSUFFICIENT_EVIDENCE:
            if draft.text != "" or draft.citation_ids or draft.claims:
                return self._save(
                    package, AnswerStatus.SYSTEM_ERROR, warnings=("GENERATION_PROTOCOL_INVALID",)
                )
            # A refusal has no factual claims to verify and must never enter the
            # answer cache. It still crosses the same current-permission release gate.
            with self.response_release_guard():
                if not self._permission_recheck(package, subject_scope_tokens, clearance_level):
                    return self._save(
                        package,
                        AnswerStatus.SYSTEM_ERROR,
                        warnings=("FINAL_PERMISSION_RECHECK_FAILED",),
                    )
                return self._save(
                    package,
                    AnswerStatus.INSUFFICIENT_EVIDENCE,
                    warnings=("MODEL_INSUFFICIENT_EVIDENCE",),
                    verified=True,
                )
        if draft.status is not DraftAnswerStatus.ANSWERED:
            return self._save(
                package, AnswerStatus.SYSTEM_ERROR, warnings=("GENERATION_PROTOCOL_INVALID",)
            )
        available = {item.evidence_id: item for item in package.generation_evidence}
        if (
            not draft.text.strip()
            or not draft.citation_ids
            or not draft.claims
            or len(set(draft.citation_ids)) != len(draft.citation_ids)
            or any(evidence_id not in available for evidence_id in draft.citation_ids)
        ):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("CITATION_VALIDATION_FAILED",),
            )
        claim_citation_ids = tuple(
            dict.fromkeys(
                evidence_id for claim in draft.claims for evidence_id in claim.evidence_ids
            )
        )
        if (
            not claim_citation_ids
            or any(evidence_id not in available for evidence_id in claim_citation_ids)
            or not set(claim_citation_ids).issubset(draft.citation_ids)
        ):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("CLAIM_CITATION_VALIDATION_FAILED",),
            )
        cited = tuple(available[evidence_id] for evidence_id in claim_citation_ids)
        try:
            for claim in draft.claims:
                visual_claim_evidence(claim, package.generation_evidence)
        except ValueError as error:
            return self._save(package, AnswerStatus.SYSTEM_ERROR, warnings=(str(error),))
        if not self._permission_recheck(package, subject_scope_tokens, clearance_level):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("PRE_VERIFIER_PERMISSION_RECHECK_FAILED",),
            )
        surface_repair_attempted = False
        verification_phase = "claims_and_conflicts"

        def repair_surface_once(
            current: DraftAnswer, checked: VerificationResult
        ) -> tuple[DraftAnswer, VerificationResult]:
            nonlocal surface_repair_attempted, verification_phase
            rebuild = repairable_surface(current, checked)
            if surface_repair_attempted or not (
                rebuild
                or (
                    isinstance(self.generator, CitationRepairPort)
                    and repairable_citations(current, checked)
                )
            ):
                return current, checked
            surface_repair_attempted = True
            verification_phase = "answer_surface_repair" if rebuild else "citation_repair"
            if not self._permission_recheck(package, subject_scope_tokens, clearance_level):
                raise InvalidProviderResponse("CITATION_REPAIR_SOURCE_INVALID")
            if rebuild:
                with self.tracer.span("rag.ask.answer_surface.rebuild"):
                    patched = (
                        list_plan.draft
                        if list_plan is not None and current.claims == list_plan.draft.claims
                        else rebuild_from_supported_claims(current)
                    )
            else:
                assert isinstance(self.generator, CitationRepairPort)
                with self.tracer.span("rag.ask.citations.repair"):
                    patched = self.generator.repair_citations(question, current, package.evidence)
                    validate_citation_only_change(current, patched)
            if patched == current:
                return current, checked
            verification_phase = "claims_and_conflicts"
            with self.tracer.span(
                "rag.ask.answer_surface.reverify" if rebuild else "rag.ask.citations.reverify"
            ):
                return patched, self.verifier.verify(question, patched, package.evidence)

        try:
            with self.tracer.span("rag.ask.claim.verify"):
                verification = self.verifier.verify(question, draft, package.evidence)
            draft, verification = repair_surface_once(draft, verification)
            if (
                any(c["status"] == "missing" for c in verification.condition_checks)
                and not verification.conflicting_evidence_ids
            ):
                # One bounded repair; use source quotes rather than the verifier's free-form
                # explanation. The repaired answer crosses every verification gate again.
                repair_evidence = condition_repair_evidence(
                    package,
                    verification.condition_checks,
                    cited_ids=claim_citation_ids,
                    max_tokens=max(
                        8000,
                        int(
                            getattr(
                                getattr(self.generator, "_settings", None),
                                "overview_evidence_tokens",
                                8000,
                            )
                        ),
                    )
                    if package.coverage_report.get("mode") == "overview"
                    else 8000,
                )
                verification_phase = "condition_repair"
                with self.tracer.span("rag.ask.conditions.repair"):
                    repaired = append_missing_text_conditions(draft, verification, repair_evidence)
                    if repaired is None:
                        repaired = self.generator.generate(question, repair_evidence)
                if repaired.status is DraftAnswerStatus.ANSWERED:
                    available = {e.evidence_id: e for e in repair_evidence}
                    repaired_ids = tuple(
                        dict.fromkeys(i for c in repaired.claims for i in c.evidence_ids)
                    )
                    if (
                        not repaired.text.strip()
                        or not repaired.claims
                        or not repaired.citation_ids
                        or len(set(repaired.citation_ids)) != len(repaired.citation_ids)
                        or not repaired_ids
                        or any(i not in available for i in repaired.citation_ids)
                        or not set(repaired_ids).issubset(repaired.citation_ids)
                    ):
                        raise InvalidProviderResponse("CONDITION_REPAIR_CITATIONS_INVALID")
                    if not self._permission_recheck(package, subject_scope_tokens, clearance_level):
                        return self._save(
                            package,
                            AnswerStatus.SYSTEM_ERROR,
                            warnings=("PRE_VERIFIER_PERMISSION_RECHECK_FAILED",),
                        )
                    draft, claim_citation_ids = repaired, repaired_ids
                    for claim in draft.claims:
                        visual_claim_evidence(claim, repair_evidence)
                    cited = tuple(available[i] for i in repaired_ids)
                    package = replace(
                        package,
                        evidence=tuple(
                            available.get(e.evidence_id, replace(e, source_role="conflict_context"))
                            for e in package.evidence
                        ),
                    )
                    verification_phase = "claims_and_conflicts"
                    with self.tracer.span("rag.ask.claim.reverify"):
                        verification = self.verifier.verify(question, draft, package.evidence)
                    draft, verification = repair_surface_once(draft, verification)
        except TransientProviderError as error:
            record_failure(
                "verification",
                error.code,
                {
                    "draft": asdict(draft),
                    "detail": {
                        "verification_stage": verification_phase,
                        **getattr(error, "diagnostic", {}),
                    },
                },
            )
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("CLAIM_VERIFIER_UNAVAILABLE",)
                + (("CLAIM_VERIFIER_TIMEOUT",) if isinstance(error, ProviderTimeout) else ())
                + (
                    (error.code,)
                    if isinstance(error, ProviderRateLimited)
                    and error.code
                    in {"MODEL_PROVIDER_RATE_LIMITED", "MODEL_ACCOUNT_REQUEST_EXCEEDS_TOKEN_BUDGET"}
                    else ()
                ),
                retryable=True,
            )
        except (InvalidProviderResponse, ValueError) as error:
            record_failure(
                "verification",
                error.code
                if isinstance(error, InvalidProviderResponse)
                else "VERIFIER_PROTOCOL_INVALID",
                {
                    "detail": {
                        "verification_stage": verification_phase,
                        **getattr(error, "diagnostic", {}),
                    },
                    "draft": asdict(draft),
                },
            )
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("CLAIM_VERIFIER_PROTOCOL_INVALID", str(error))
                if str(error).startswith(("VERIFIER_CONDITION_", "CITATION_REPAIR_"))
                or str(error)
                in {
                    "VERIFIER_VERDICT_COUNT_INVALID",
                    "VERIFIER_CLAIM_ID_INVALID",
                    "VERIFIER_CONTENT_NOT_JSON",
                    "VERIFIER_ANSWER_CHECK_REQUIRED",
                    "VERIFIER_CONFLICT_CHECK_REQUIRED",
                    "VERIFIER_CONFLICT_WITNESS_INVALID",
                    "MODEL_PROVIDER_HTTP_ERROR",
                    "MODEL_PROVIDER_MODEL_UNSUPPORTED",
                }
                else ("CLAIM_VERIFIER_PROTOCOL_INVALID",),
            )
        if verification.conflicting_evidence_ids:
            record_failure(
                "verification",
                "UNRESOLVED_POLICY_CONFLICT",
                {"draft": asdict(draft), "verification": asdict(verification)},
            )
            with self.response_release_guard():
                if not self._permission_recheck(package, subject_scope_tokens, clearance_level):
                    return self._save(
                        package,
                        AnswerStatus.SYSTEM_ERROR,
                        warnings=("FINAL_PERMISSION_RECHECK_FAILED",),
                    )
                return self._save(
                    package,
                    AnswerStatus.CONFLICTING_EVIDENCE,
                    warnings=("UNRESOLVED_POLICY_CONFLICT",),
                    verified=True,
                )
        if not verification.supported:
            record_failure(
                "verification",
                "ANSWER_NOT_SUPPORTED",
                {"draft": asdict(draft), "verification": asdict(verification)},
            )
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR
                if not verification.citation_ids_valid
                else AnswerStatus.INSUFFICIENT_EVIDENCE,
                warnings=tuple(
                    dict.fromkeys(
                        (
                            "ANSWER_NOT_SUPPORTED",
                            *(
                                item.reason_code
                                for item in verification.verdicts
                                if item.verdict != "SUPPORTED"
                            ),
                            *(
                                ("ANSWER_KEY_CONDITION_MISSING",)
                                if any(
                                    c["status"] == "missing" for c in verification.condition_checks
                                )
                                else ()
                            ),
                            *(
                                ("ANSWER_CITATION_COVERAGE_INVALID",)
                                if (
                                    not verification.citation_ids_valid
                                    or not verification.answer_claims_covered
                                )
                                else ()
                            ),
                        )
                    )
                ),
                condition_checks=verification.condition_checks,
            )
        if list_plan is not None:
            if not list_plan.preserves_items(draft):
                record_failure("answer_validation", "SOURCE_LIST_COVERAGE_INCOMPLETE")
                return self._save(
                    package,
                    AnswerStatus.SYSTEM_ERROR,
                    warnings=("SOURCE_LIST_COVERAGE_INCOMPLETE",),
                )
            package = replace(
                package,
                coverage_report={
                    **package.coverage_report,
                    "source_list": {
                        **package.coverage_report["source_list"],
                        "rendered_items": len(list_plan.draft.claims),
                        "complete": True,
                    },
                },
            )
        verified_draft = DraftAnswer(
            draft.text if draft.synthesized else render_verified_claims(draft.claims),
            claim_citation_ids,
            draft.claims,
            synthesized=draft.synthesized,
        )
        if not verified_draft.text:
            return self._save(
                package,
                AnswerStatus.INSUFFICIENT_EVIDENCE,
                warnings=("VERIFIED_CLAIMS_EMPTY",),
            )
        package = replace(
            package,
            evidence=tuple(
                replace(
                    e,
                    locator={
                        **e.locator,
                        "used_visual_fact_ids": used_visual_fact_ids(draft.claims, e),
                    },
                )
                if e.locator.get("visual_facts") and e.evidence_id in claim_citation_ids
                else e
                for e in package.evidence
            ),
        )
        cited = tuple(e for e in package.evidence if e.evidence_id in claim_citation_ids)
        # All model calls have finished. Runtime assembly shares this guard with
        # lifecycle mutations, serializing the release decision and its side effects
        # against revocation in this runtime. HTTP transport is outside this boundary.
        with self.response_release_guard():
            with self.tracer.span("rag.ask.permission.final"):
                allowed = self._permission_recheck(package, subject_scope_tokens, clearance_level)
            if not allowed:
                return self._save(
                    package,
                    AnswerStatus.SYSTEM_ERROR,
                    warnings=("FINAL_PERMISSION_RECHECK_FAILED",),
                )
            with self.tracer.span("rag.ask.citation.verify"):
                citations = tuple(
                    Citation(
                        evidence_id=evidence.evidence_id,
                        source_url=self.references.source_url(
                            package.rag_run_id,
                            evidence.evidence_id,
                            package.tenant_id,
                            user_id,
                            evidence.document_id,
                        ),
                        locator=evidence.locator,
                    )
                    for evidence in cited
                )
            if self.cache is not None and use_cache:
                self.cache.put(package, verified_draft)
            return self._save(
                package,
                AnswerStatus.ANSWERED,
                answer=verified_draft.text,
                citations=citations,
                verified=True,
                condition_checks=verification.condition_checks,
            )

    def ask(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        *,
        subject_scope_tokens: tuple[str, ...] = (),
        clearance_level: int = 0,
        space_id: str | None = None,
    ) -> AskResult:
        from ragkb.application.qa_performance import performance_scope

        with (
            performance_scope(),
            diagnostic_scope(),
            self.tracer.span(
                "rag.ask", {"tenant_id": tenant_id, "space_id": space_id or "default"}
            ),
        ):
            ask = (
                self._ask
                if self.result_reuse is None
                else lambda *args, **kwargs: self.result_reuse.execute(self, *args, **kwargs)
            )
            return ask(
                question,
                tenant_id,
                user_id,
                subject_scope_tokens=subject_scope_tokens,
                clearance_level=clearance_level,
                space_id=space_id,
            )

    def feedback(
        self,
        rag_run_id: str,
        user_id: str,
        rating: int,
        reason_code: str,
        comment: str,
    ) -> Feedback:
        result = self.repository.get_result(rag_run_id)
        if result is None:
            raise KeyError(rag_run_id)
        package = self.repository.get_package(rag_run_id)
        if package is None:
            raise KeyError(rag_run_id)
        if package.user_id != user_id:
            raise KeyError(rag_run_id)
        feedback = Feedback(
            rag_run_id=rag_run_id,
            user_id=user_id,
            rating=rating,
            reason_code=reason_code,
            comment=comment,
            index_generation_id=package.index_generation_id,
            retrieval_revision=package.retrieval_revision,
            prompt_revision=package.prompt_revision,
            model_revision=package.model_revision,
        )
        self.repository.save_feedback(feedback)
        return feedback


class InMemoryVerifiedAnswerCache:
    """Caches only verified drafts and naturally invalidates on evidence or revision changes."""

    def __init__(self, *, max_entries: int = 1024) -> None:
        self.max_entries = max_entries
        self._values: dict[str, DraftAnswer] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(package: EvidencePackage) -> str:
        return verified_answer_cache_key(package)

    def get(self, package: EvidencePackage) -> DraftAnswer | None:
        with self._lock:
            return self._values.get(self._key(package))

    def put(self, package: EvidencePackage, draft: DraftAnswer) -> None:
        if draft.status is not DraftAnswerStatus.ANSWERED:
            return
        key = self._key(package)
        with self._lock:
            if len(self._values) >= self.max_entries:
                self._values.pop(next(iter(self._values)))
            self._values[key] = draft


def verified_answer_cache_key(package: EvidencePackage) -> str:
    payload = {
        "verifier_revision": package.verifier_revision,
        "permission_policy_revision": "account-scoped-final-release-v1",
        "subject_authorization_revision": package.subject_authorization_revision,
        "tenant_id": package.tenant_id,
        "user_id": package.user_id,
        "permission_revision": package.permission_revision,
        "query": " ".join(package.query.casefold().split()),
        "index_generation_id": package.index_generation_id,
        "retrieval_revision": package.retrieval_revision,
        "prompt_revision": package.prompt_revision,
        "model_revision": package.model_revision,
        "evidence": [
            asdict(
                replace(
                    item,
                    locator={
                        key: value
                        for key, value in item.locator.items()
                        if key != "used_visual_fact_ids"
                    },
                )
            )
            for item in package.evidence
        ],
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
