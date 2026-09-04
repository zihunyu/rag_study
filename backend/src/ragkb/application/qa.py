"""Trusted QA orchestration with buffered generation and final fail-closed verification."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import asdict
from typing import Literal
from urllib.parse import urlparse

from ragkb.application.tracing import InMemoryTracer, TracerPort
from ragkb.contracts.rag import (
    BufferedGenerationPort,
    CitationReferencePort,
    ClaimVerifierPort,
    EvidenceProviderPort,
    FinalPermissionPort,
    RAGRunRepositoryPort,
    VerifiedAnswerCachePort,
)
from ragkb.domain.claim_coverage import render_verified_claims, verify_answer_claim_coverage
from ragkb.domain.errors import InvalidProviderResponse, RetrievalFailClosed, TransientProviderError
from ragkb.domain.ids import new_uuid7
from ragkb.domain.rag import (
    AnswerStatus,
    AskResult,
    Citation,
    ClaimVerdict,
    DraftAnswer,
    Evidence,
    EvidencePackage,
    Feedback,
    QuestionDisposition,
    VerificationResult,
)

_FACT_PATTERN = re.compile(
    r"(?:(?:\d{1,4}(?:[-/.年]\d{1,2}){0,2}|\d+(?:\.\d+)?)|"
    r"[零一二两三四五六七八九十百千万亿]+)\s*"
    r"(?:%|元|年|月|日|天|小时|分钟|kg|公里|米)?",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://[^\s)\]}>]+", re.IGNORECASE)
_CREDENTIAL_REQUEST_PATTERN = re.compile(
    r"(?:输入|提供|发送|告知|索取).{0,12}(?:密码|验证码|口令)|"
    r"(?:provide|send|enter|share|ask for).{0,20}(?:password|verification code|credential)",
    re.IGNORECASE,
)
_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "零": "0",
    "一": "1",
    "二": "2",
    "两": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
}


def _normalized_fact_text(value: str) -> str:
    normalized = value.casefold()
    normalized = re.sub(r"\byears?\b", "年", normalized)
    normalized = re.sub(r"\bmonths?\b", "月", normalized)
    normalized = re.sub(r"\bdays?\b", "天", normalized)
    for word, number in _NUMBER_WORDS.items():
        normalized = re.sub(
            rf"\b{word}\b" if word.isascii() else re.escape(word), number, normalized
        )
    return re.sub(r"\s+", "", normalized)


class DeterministicClaimVerifier:
    """Fail-closed structural checks that run before any answer is marked verified."""

    revision = "deterministic-claim-verifier:answer-coverage:v2"

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
        if not coverage.complete:
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
            cited_evidence = tuple(item for item in cited if item is not None)
            source = "\n".join(item.text for item in cited_evidence)
            facts = tuple(
                match.group(0).strip().casefold() for match in _FACT_PATTERN.finditer(claim.text)
            )
            normalized_source = _normalized_fact_text(source)
            missing_fact = next(
                (fact for fact in facts if _normalized_fact_text(fact) not in normalized_source),
                None,
            )
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
            if missing_fact is not None:
                verdict, reason = "CONTRADICTED", "EXACT_FACT_NOT_IN_EVIDENCE"
            elif unsupported_url is not None:
                verdict, reason = "INSUFFICIENT", "UNSUPPORTED_EXTERNAL_URL"
            elif disallowed_url is not None:
                verdict, reason = "INSUFFICIENT", "OUTPUT_URL_DOMAIN_NOT_ALLOWED"
            elif asks_for_credentials:
                verdict, reason = "INSUFFICIENT", "UNSUPPORTED_CREDENTIAL_REQUEST"
            else:
                verdict, reason = "SUPPORTED", "STRUCTURE_AND_EXACT_FACTS_SUPPORTED"
            verdicts.append(ClaimVerdict(claim.text, claim.evidence_ids, verdict, reason))
        return VerificationResult(
            tuple(verdicts),
            self.revision,
            answer_claims_covered=True,
            evidence_support_verified=all(item.verdict == "SUPPORTED" for item in verdicts),
        )


class CompositeClaimVerifier:
    """Run deterministic policy checks before the independently configured model verifier."""

    def __init__(self, structural: ClaimVerifierPort, semantic: ClaimVerifierPort) -> None:
        self.structural = structural
        self.semantic = semantic
        self.revision = f"claim-verifier-chain:{structural.revision}+{semantic.revision}"

    def verify(
        self, question: str, draft: DraftAnswer, evidence: tuple[Evidence, ...]
    ) -> VerificationResult:
        structural = self.structural.verify(question, draft, evidence)
        if not structural.supported:
            return VerificationResult(
                structural.verdicts,
                self.revision,
                citation_ids_valid=structural.citation_ids_valid,
                answer_claims_covered=structural.answer_claims_covered,
                evidence_support_verified=structural.evidence_support_verified,
                conflict_checked=structural.conflict_checked,
                policy_checked=structural.policy_checked,
            )
        semantic = self.semantic.verify(question, draft, evidence)
        return VerificationResult(
            semantic.verdicts,
            self.revision,
            citation_ids_valid=structural.citation_ids_valid and semantic.citation_ids_valid,
            answer_claims_covered=(
                structural.answer_claims_covered and semantic.answer_claims_covered
            ),
            evidence_support_verified=(
                structural.evidence_support_verified and semantic.evidence_support_verified
            ),
            conflict_checked=structural.conflict_checked and semantic.conflict_checked,
            policy_checked=structural.policy_checked and semantic.policy_checked,
        )


class TrustedQAService:
    revision = "trusted-qa:g3-v1"

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
    ) -> None:
        self.evidence_provider = evidence_provider
        self.generator = generator
        self.permission = permission
        self.references = references
        self.repository = repository
        self.verifier = verifier or DeterministicClaimVerifier()
        self.cache = cache
        self.tracer = tracer or InMemoryTracer()

    def _save(
        self,
        package: EvidencePackage,
        status: AnswerStatus,
        *,
        answer: str | None = None,
        citations: tuple[Citation, ...] = (),
        warnings: tuple[str, ...] = (),
        verified: bool = False,
    ) -> AskResult:
        result = AskResult(
            rag_run_id=package.rag_run_id,
            status=status,
            answer=answer,
            citations=citations,
            evidence=package.evidence,
            warnings=warnings,
            verified=verified,
            real_acceptance=package.real_acceptance and verified,
        )
        self.repository.save_run(package, result)
        return result

    def _permission_recheck(
        self, package: EvidencePackage, subject_scope_tokens: tuple[str, ...]
    ) -> bool:
        return self.permission.recheck(
            package.evidence,
            tenant_id=package.tenant_id,
            user_id=package.user_id,
            subject_scope_tokens=subject_scope_tokens,
            permission_revision=package.permission_revision,
            at_epoch=int(time.time()),
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
        except (RetrievalFailClosed, TransientProviderError):
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
            )
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("RETRIEVAL_OR_PERMISSION_FAIL_CLOSED",),
            )
        if package.disposition is QuestionDisposition.OUT_OF_SCOPE:
            return self._save(package, AnswerStatus.OUT_OF_SCOPE, verified=True)
        if package.disposition is QuestionDisposition.NEEDS_CLARIFICATION:
            return self._save(package, AnswerStatus.NEEDS_CLARIFICATION, verified=True)
        if not package.evidence:
            return self._save(package, AnswerStatus.INSUFFICIENT_EVIDENCE, verified=True)
        if package.conflict_detected:
            return self._save(package, AnswerStatus.CONFLICTING_EVIDENCE, verified=True)
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
        if not self._permission_recheck(package, subject_scope_tokens):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("PRE_GENERATION_PERMISSION_RECHECK_FAILED",),
            )
        draft = self.cache.get(package) if self.cache is not None else None
        try:
            if draft is None:
                with self.tracer.span("rag.ask.llm.generate"):
                    draft = self.generator.generate(question, package.evidence)
        except (TransientProviderError, InvalidProviderResponse):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("GENERATION_UNAVAILABLE_AUTHORIZED_EVIDENCE_ONLY",),
            )
        available = {item.evidence_id: item for item in package.evidence}
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
        if not self._permission_recheck(package, subject_scope_tokens):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("FINAL_PERMISSION_RECHECK_FAILED",),
            )
        try:
            with self.tracer.span("rag.ask.claim.verify"):
                verification = self.verifier.verify(question, draft, cited)
        except (TransientProviderError, InvalidProviderResponse, ValueError):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("CLAIM_VERIFIER_UNAVAILABLE",),
            )
        if not verification.supported:
            return self._save(
                package,
                AnswerStatus.INSUFFICIENT_EVIDENCE,
                warnings=tuple(item.reason_code for item in verification.verdicts),
            )
        verified_draft = DraftAnswer(
            render_verified_claims(draft.claims),
            claim_citation_ids,
            draft.claims,
        )
        if not verified_draft.text:
            return self._save(
                package,
                AnswerStatus.INSUFFICIENT_EVIDENCE,
                warnings=("VERIFIED_CLAIMS_EMPTY",),
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
        if self.cache is not None:
            self.cache.put(package, verified_draft)
        return self._save(
            package,
            AnswerStatus.ANSWERED,
            answer=verified_draft.text,
            citations=citations,
            verified=True,
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
        with self.tracer.span(
            "rag.ask", {"tenant_id": tenant_id, "space_id": space_id or "default"}
        ):
            return self._ask(
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
        key = self._key(package)
        with self._lock:
            if len(self._values) >= self.max_entries:
                self._values.pop(next(iter(self._values)))
            self._values[key] = draft


def verified_answer_cache_key(package: EvidencePackage) -> str:
    payload = {
        "tenant_id": package.tenant_id,
        "user_id": package.user_id,
        "permission_revision": package.permission_revision,
        "query": " ".join(package.query.casefold().split()),
        "index_generation_id": package.index_generation_id,
        "retrieval_revision": package.retrieval_revision,
        "prompt_revision": package.prompt_revision,
        "model_revision": package.model_revision,
        "evidence": [asdict(item) for item in package.evidence],
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
