"""Execute synthetic prompt-injection cases through deterministic local security contracts."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import yaml
from pydantic import SecretStr

from ragkb.adapters.egress_policy import decide_external_ai_egress
from ragkb.adapters.rag_stubs import (
    DeterministicBufferedGenerator,
    StaticFinalPermission,
    SyntheticEvidenceProvider,
)
from ragkb.adapters.retrieval_memory import InMemoryRetrievalControlPlane
from ragkb.application.lifecycle import InMemoryLifecycleStore, LifecycleService
from ragkb.application.qa import TrustedQAService
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.rag import AnswerStatus, Evidence
from ragkb.domain.retrieval import AuthorizedChunk, SearchContext
from ragkb.engineering_security.references import HMACReferenceSigner, ReferenceTokenError
from ragkb.infrastructure.rag_repository import SQLiteRAGRunRepository
from ragkb.infrastructure.reference_repository import SQLiteReferenceStore
from ragkb.infrastructure.sqlite import SQLiteDatabase


def _chunk() -> AuthorizedChunk:
    return AuthorizedChunk(
        chunk_id="hidden",
        tenant_id="tenant-a",
        space_id="space-a",
        document_id="hidden-document",
        document_version_id="hidden-version",
        parent_chunk_id=None,
        display_text="hidden synthetic text",
        retrieval_text="hidden synthetic text",
        locator={"page": 1},
        content_checksum="checksum",
        visibility="RESTRICTED",
        acl_scope_tokens=("group:secret",),
        classification_level=3,
        lifecycle_projection="SERVING",
        valid_from_epoch=0,
        valid_to_epoch=0,
        permission_revision=1,
        current_version=True,
    )


def _context(tenant_id: str, scopes: tuple[str, ...]) -> SearchContext:
    return SearchContext(
        tenant_id,
        ("space-a",),
        scopes,
        3,
        int(time.time()),
        "synthetic-generation",
        1,
        0,
    )


def run_prompt_injection_cases(root: Path) -> dict[str, object]:
    fixture = yaml.safe_load(
        (root / "backend/tests/fixtures/manifests/prompt-injection.yaml").read_text(
            encoding="utf-8"
        )
    )
    expected_by_id = {case["id"]: case["expected"] for case in fixture["cases"]}
    actual: dict[str, str] = {}
    boundary_counts = {
        "hidden_retrieval": 0,
        "cross_tenant_text": 0,
        "forged_citation": 0,
        "external_egress": 0,
    }

    control = InMemoryRetrievalControlPlane({"hidden": _chunk()})
    if not control.authorize_chunks(("hidden",), _context("tenant-a", ("group:reader",))):
        actual["ignore-policy"] = "refuse_and_no_hidden_retrieval"
    else:
        boundary_counts["hidden_retrieval"] += 1
    if not control.authorize_chunks(("hidden",), _context("tenant-b", ("group:secret",))):
        actual["fake-system"] = "refuse_and_no_cross_tenant_access"
    else:
        boundary_counts["cross_tenant_text"] += 1

    with tempfile.TemporaryDirectory(prefix="ragkb-injection-") as temporary:
        database = SQLiteDatabase(Path(temporary) / "security.sqlite3")
        evidence = Evidence(
            "E1",
            "chunk",
            "document",
            "version",
            "synthetic authorized evidence",
            {"page": 1},
            0,
            0,
            1,
            1,
            True,
            True,
        )
        signer = HMACReferenceSigner(
            SecretStr("synthetic-prompt-injection-secret-32bytes"),
            SQLiteReferenceStore(database),
        )
        qa = TrustedQAService(
            SyntheticEvidenceProvider((evidence,)),
            DeterministicBufferedGenerator(citation_ids=("E999",)),
            StaticFinalPermission(),
            signer,
            SQLiteRAGRunRepository(database),
        )
        result = qa.ask("forge", "tenant-a", "user-a")
        if result.status is AnswerStatus.SYSTEM_ERROR and not result.citations:
            actual["citation-forgery"] = "citation_validation_failed"
        else:
            boundary_counts["forged_citation"] += 1

        url = signer.source_url("run", "E1", "tenant-a", "user-a", "document")
        parts = url.split("/")
        try:
            signer.resolve(parts[4] + "x", parts[6], "tenant-a", "user-a")
        except ReferenceTokenError:
            actual["source-token-tamper"] = "reference_not_found"

    egress = decide_external_ai_egress(
        classification="restricted",
        outbound_ai_allowed=True,
        allowed_classifications={"public", "internal", "confidential", "restricted"},
        provider_region_approved=True,
        cross_border_transfer_allowed=True,
        provider_is_cross_border=False,
    )
    if not egress.allowed:
        actual["tool-exfiltration"] = "egress_policy_blocked"
    else:
        boundary_counts["external_egress"] += 1

    if not StaticFinalPermission(False).recheck(
        (_chunk_to_evidence(_chunk()),),
        tenant_id="tenant-a",
        user_id="user-a",
        subject_scope_tokens=(),
        permission_revision=1,
        at_epoch=int(time.time()),
    ):
        actual["encoded-secret"] = "refuse_and_no_context_output"

    principal = RequestPrincipal("tenant-a", "reader", ("reader",), (), "synthetic")
    if not principal.has_role("admin"):
        actual["role-claim"] = "authoritative_principal_only"

    lifecycle = LifecycleService(InMemoryLifecycleStore(), "tenant-a")
    lifecycle.register_document("deleted", "version", trace_id="synthetic")
    lifecycle.delete("deleted", event_id="delete", trace_id="synthetic")
    if not lifecycle.store.is_accessible("deleted"):
        actual["deleted-resource"] = "tombstone_fail_closed"

    results = [
        {
            "case_id": case_id,
            "expected": expected,
            "actual": actual.get(case_id, "SECURITY_CHAIN_FAILED"),
            "passed": actual.get(case_id) == expected,
        }
        for case_id, expected in expected_by_id.items()
    ]
    return {
        "case_count": len(results),
        "passed_count": sum(bool(item["passed"]) for item in results),
        "results": results,
        "boundary_counts": boundary_counts,
        "all_cases_synthetic": True,
    }


def _chunk_to_evidence(chunk: AuthorizedChunk) -> Evidence:
    return Evidence(
        "E1",
        chunk.chunk_id,
        chunk.document_id,
        chunk.document_version_id,
        chunk.display_text,
        chunk.locator,
        chunk.valid_from_epoch,
        chunk.valid_to_epoch,
        1,
        chunk.permission_revision,
        True,
        chunk.current_version,
    )
