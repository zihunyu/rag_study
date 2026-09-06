"""Atomic storage for structured-claim results and audit manifests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload, usedforsecurity=False).hexdigest()


class ClaimArtifactStore:
    def __init__(self, artifacts_root: Path, *, run_id: str = "default") -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", run_id):
            raise ValueError("UAT_RUN_ID_INVALID")
        self.claim_revision = run_id
        self.claim_audit_root = (artifacts_root / "uat-claim-audits" / run_id).resolve()
        self.claim_result_root = (artifacts_root / "uat-claim-results" / run_id).resolve()
        self.claim_coverage_path = self.claim_audit_root / "coverage.json"

    @staticmethod
    def _safe_audit_id(value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", value):
            raise ValueError("UAT_AUDIT_CASE_ID_INVALID")
        return value

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def persist_claim_audit_manifest(
        self, test_case_id: str, manifest: Mapping[str, Any]
    ) -> dict[str, object]:
        """Persist a content-free, immutable audit record for an acceptance case."""

        safe_id = self._safe_audit_id(test_case_id)
        record = dict(manifest)
        if (
            record.get("test_case_id") != safe_id
            or record.get("revision") != "uat-audit-manifest"
            or record.get("content_output") is not False
        ):
            raise ValueError("UAT_AUDIT_MANIFEST_INVALID")
        payload = (
            json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode()
        if any(token in payload for token in (b'"content"', b'"answer"', b'"question"')):
            raise ValueError("UAT_AUDIT_MANIFEST_CONTENT_FORBIDDEN")
        path = self.claim_audit_root / f"{safe_id}.json"
        if path.is_file() and path.read_bytes() != payload:
            raise ValueError("UAT_AUDIT_MANIFEST_IMMUTABLE_MISMATCH")
        if not path.is_file():
            self._atomic_write(path, payload)
        return {
            "test_case_id": safe_id,
            "audit_ref": f"uat-claim-audits/{self.claim_revision}/{safe_id}.json",
            "audit_sha256": _sha256(payload),
            "audit_bytes": len(payload),
        }

    def read_claim_audit_manifest(self, test_case_id: str) -> dict[str, Any]:
        safe_id = self._safe_audit_id(test_case_id)
        path = self.claim_audit_root / f"{safe_id}.json"
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(loaded, dict)
            or loaded.get("revision") != "uat-audit-manifest"
            or loaded.get("test_case_id") != safe_id
        ):
            raise ValueError("UAT_AUDIT_MANIFEST_INVALID")
        return loaded

    def persist_claim_coverage_manifest(self, manifest: Mapping[str, Any]) -> dict[str, object]:
        record = dict(manifest)
        if (
            record.get("revision") != "uat-audit-coverage-manifest"
            or record.get("content_output") is not False
        ):
            raise ValueError("UAT_AUDIT_COVERAGE_MANIFEST_INVALID")
        payload = (
            json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode()
        if any(token in payload for token in (b'"content"', b'"answer"', b'"question"')):
            raise ValueError("UAT_AUDIT_MANIFEST_CONTENT_FORBIDDEN")
        path = self.claim_coverage_path
        if path.is_file() and path.read_bytes() != payload:
            raise ValueError("UAT_AUDIT_COVERAGE_MANIFEST_IMMUTABLE_MISMATCH")
        if not path.is_file():
            self._atomic_write(path, payload)
        return {
            "coverage_ref": f"uat-claim-audits/{self.claim_revision}/coverage.json",
            "coverage_sha256": _sha256(payload),
            "coverage_bytes": len(payload),
        }

    def read_claim_coverage_manifest(self) -> dict[str, Any] | None:
        path = self.claim_coverage_path
        if not path.is_file():
            return None
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or loaded.get("revision") != "uat-audit-coverage-manifest":
            raise ValueError("UAT_AUDIT_COVERAGE_MANIFEST_INVALID")
        return loaded

    def persist_claim_result(
        self, test_case_id: str, result: Mapping[str, Any]
    ) -> dict[str, object]:
        """Persist a future-only structured-claim result outside historical result roots."""

        safe_id = self._safe_audit_id(test_case_id)
        record = dict(result)
        if (
            record.get("test_case_id") != safe_id
            or record.get("revision") != "uat-claim-result"
            or record.get("user_review_status") != "PENDING_USER_RESULT_REVIEW"
        ):
            raise ValueError("UAT_CLAIM_RESULT_INVALID")
        payload = (
            json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode()
        path = self.claim_result_root / f"{safe_id}.json"
        if path.is_file() and path.read_bytes() != payload:
            raise ValueError("UAT_CLAIM_RESULT_IMMUTABLE_MISMATCH")
        if not path.is_file():
            self._atomic_write(path, payload)
        return {
            "test_case_id": safe_id,
            "result_ref": f"uat-claim-results/{self.claim_revision}/{safe_id}.json",
            "result_sha256": _sha256(payload),
            "result_bytes": len(payload),
        }
