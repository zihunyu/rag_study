from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from mysql_sql_harness import SQLControl
from pydantic import SecretStr
from ragkb.adapters.mysql_governance import MySQLGovernanceRepository
from ragkb.adapters.mysql_lifecycle import MySQLLifecycleStore
from ragkb.adapters.mysql_rag import MySQLRAGRunRepository
from ragkb.adapters.mysql_references import MySQLReferenceStore
from ragkb.adapters.mysql_upload import MySQLUploadRepository
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.application.lifecycle import LifecycleService
from ragkb.document_processing.parsers import PlainTextParser
from ragkb.domain.state_machines import UploadSessionState
from ragkb.domain.uploads import OptimisticConcurrencyError, ResourceNotFoundError
from ragkb.domain.validation import DocumentQualityReport
from ragkb.engineering_security.references import HMACReferenceSigner
from test_trusted_qa import _evidence, _service


def upload(control, tmp_path):
    repository = MySQLUploadRepository(control, "tenant", "g1")
    repository.ensure_local_hierarchy("tenant", "default", space_id_override="kb")
    payload = b"Actual bounded repository evidence."
    session = repository.create_upload_session(
        tenant_id="tenant",
        space_id="kb",
        filename="sample.txt",
        expected_size=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        declared_mime="text/plain",
        idempotency_key="session",
        request_hash="hash",
    )
    uploaded = repository.update_session(
        session.id, session.row_version, UploadSessionState.UPLOADED
    )
    validated = repository.update_session(
        session.id,
        uploaded.row_version,
        UploadSessionState.VALIDATED,
        detected_mime="text/plain",
        detected_format="txt",
    )
    promoted = repository.update_session(
        session.id,
        validated.row_version,
        UploadSessionState.PROMOTED,
        original_key="original/sample.txt",
    )
    document_id, version_id = repository.ensure_document_version(promoted)
    path = tmp_path / "sample.txt"
    path.write_bytes(payload)
    document = PlainTextParser("txt").parse(path, version_id)
    repository.save_canonical_document(document)
    repository.mark_index_ready(version_id)
    repository.save_quality_report(DocumentQualityReport.from_document(document))
    return repository, document_id, version_id, session


def test_mysql_upload_addressed_rows_review_gate_restart_and_failures(tmp_path):
    control = SQLControl(tmp_path / "mysql-behavior.sqlite3")
    repository, document, version, session = upload(control, tmp_path)
    assert repository.get_document_space(document) == "kb"
    assert repository.ensure_local_hierarchy("tenant", "default")[1] == "kb"
    assert repository.get_versions(document)[0]["id"] == version
    assert repository.list_spaces()[0]["id"] == "kb"
    assert not repository.publication_readiness(document, version).ready
    report = repository.get_quality_report(version)
    review = repository.save_document_review(
        version_id=version,
        reviewer_id="reviewer",
        decision="APPROVED",
        comment="approved",
        quality_revision=report["parser_revision"],
        security_revision="s1",
        security_projection={"visibility": "TENANT"},
        idempotency_key="review",
        request_hash="rhash",
    )
    assert (
        repository.publication_readiness(document, version).error_code
        == "PUBLICATION_SECURITY_PROJECTION_PENDING"
    )
    repository.mark_review_applied(version, review["review_id"])
    assert repository.publication_readiness(document, version).ready
    restarted = MySQLUploadRepository(control, "tenant", "g1")
    assert restarted.get_latest_review(version)["projection_applied"]
    control.statements.clear()
    assert restarted.get_session(session.id).id == session.id
    assert all(
        "entity_type=%s" in sql for sql, _ in control.statements if "SELECT entity_type" in sql
    )
    with pytest.raises(OptimisticConcurrencyError):
        repository.update_session(session.id, 1, UploadSessionState.FAILED)
    with pytest.raises(ResourceNotFoundError):
        repository.get_document("missing")
    with pytest.raises(OptimisticConcurrencyError):
        repository.mark_review_applied(version, "old-review")
    repository.record_local_content(document, version, "artifacts", "path", "canonical")
    assert ("artifacts", "path") in repository.list_local_content_lineage(document)
    repository.mark_version_failed(version, "parser")
    assert repository.get_version(version)["processing_state"] == "FAILED"
    repository.mark_version_processing(version)
    repository.mark_version_quarantined(version, "parser")
    repository.mark_version_cancelled(version)
    control.fail_match = "UPDATE upload_entities_v3"
    with pytest.raises(ConnectionError):
        repository.mark_version_processing(version)
    control.fail_match = None
    assert repository.get_version(version)["processing_state"] == "CANCELLED"


def test_mysql_stale_lifecycle_does_not_delete_newer_rows_or_restore_revoked(tmp_path):
    control = SQLControl(tmp_path / "mysql-behavior.sqlite3")
    first = MySQLLifecycleStore(control, "tenant")
    service = LifecycleService(first, "tenant")
    service.register_document("a", "v1", trace_id="seed")
    stale = MySQLLifecycleStore(control, "tenant")
    stale.reload()
    service.register_document("b", "v2", trace_id="new")
    service.revoke("a", event_id="revoke", trace_id="trace")
    stale.documents["a"].visible = True
    with pytest.raises(OptimisticConcurrencyError):
        stale.persist_state(tenant_id="tenant")
    actual = MySQLLifecycleStore(control, "tenant")
    actual.reload()
    assert "b" in actual.documents
    assert not actual.is_accessible("a")
    assert not any("DELETE FROM lifecycle_entities_v3" in sql for sql, _ in control.statements)


def test_mysql_run_reference_feedback_round_trip(tmp_path):
    control = SQLControl(tmp_path / "mysql-behavior.sqlite3")
    repository = MySQLRAGRunRepository(control)
    reference = MySQLReferenceStore(control)
    service, original, _ = _service(tmp_path, SyntheticEvidenceProvider((_evidence(),)))
    answer = service.ask("保修期多久？", "tenant-1", "user-1")
    package = original.get_package(answer.rag_run_id)
    repository.save_run(package, answer)
    assert repository.get_result(answer.rag_run_id) == answer
    assert repository.get_package(answer.rag_run_id) == package
    assert repository.get_evidence(answer.rag_run_id, "E1") == _evidence()
    assert repository.get_result("missing") is None
    signer = HMACReferenceSigner(SecretStr("a-valid-reference-test-key-32bytes"), reference)
    url = signer.source_url(answer.rag_run_id, "E1", "tenant-1", "user-1", _evidence().document_id)
    parts = url.split("/")
    assert signer.resolve(parts[4], parts[6], "tenant-1", "user-1") == (answer.rag_run_id, "E1")
    assert reference.revoke_document(_evidence().document_id) >= 1


def test_mysql_access_event_is_single_row_insert_not_tenant_read(tmp_path):
    control = SQLControl(tmp_path / "mysql-behavior.sqlite3")
    repository = MySQLGovernanceRepository(control, "tenant")
    for number in range(12):
        repository.record_event(str(number), "request.completed", "INFO", {"status": 200})
    assert not any(sql.lstrip().startswith("SELECT") for sql, _ in control.statements)
    assert repository.diagnostics()["event_count"] == 12


@pytest.mark.parametrize(
    "scenario",
    [
        "test_governance_idempotency_is_stable_conflicting_and_restart_safe",
        "test_pilot_requires_canary_uat_signoffs_and_rollout_is_idempotent",
        "test_uat_evidence_and_observation_api_fail_closed",
    ],
)
def test_mysql_governance_runs_the_same_behavior_matrix(tmp_path, monkeypatch, scenario):
    import test_governance_api as shared
    from fastapi.testclient import TestClient
    from ragkb.api.app import create_app
    from ragkb.application.governance import GovernanceService
    from ragkb.runtime_components import build_runtime_components

    sql = SQLControl(tmp_path / "governance.sqlite3")

    def client(folder):
        runtime = build_runtime_components(
            storage_root=folder / "storage", database_path=folder / "local.sqlite3"
        )
        repository = MySQLGovernanceRepository(sql, runtime.tenant_id)
        return TestClient(
            create_app(
                replace(
                    runtime,
                    governance_repository=repository,
                    governance_service=GovernanceService(repository),
                )
            )
        )

    monkeypatch.setattr(shared, "_client", client)
    getattr(shared, scenario)(tmp_path)
