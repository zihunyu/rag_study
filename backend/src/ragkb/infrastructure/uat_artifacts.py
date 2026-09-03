"""Atomic local-only storage for UAT bundles and future model results."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload, usedforsecurity=False).hexdigest()


class LocalUatArtifactStore:
    def __init__(
        self,
        artifacts_root: Path,
        *,
        bundle_revision: str = "v1",
        result_revision: str = "v1",
        claim_revision: str = "v1",
    ) -> None:
        if bundle_revision not in {"v1", "v2"}:
            raise ValueError("UAT_BUNDLE_REVISION_INVALID")
        if result_revision not in {"v1", "v2", "v3", "v4"}:
            raise ValueError("UAT_RESULT_REVISION_INVALID")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", claim_revision):
            raise ValueError("UAT_CLAIM_REVISION_INVALID")
        self.bundle_revision = bundle_revision
        self.result_revision = result_revision
        self.claim_revision = claim_revision
        self.bundle_root = (artifacts_root / "uat-bundles" / bundle_revision).resolve()
        self.result_root = (artifacts_root / "uat-results" / result_revision).resolve()
        self.review_root = (artifacts_root / "uat-result-review").resolve()
        self.diagnostic_bundle_root = (artifacts_root / "uat-diagnostic-bundles" / "v2").resolve()
        self.systematic_revision_root = (artifacts_root / "uat-systematic-revision-v4").resolve()
        self.systematic_revision_v5_root = (artifacts_root / "uat-systematic-revision-v5").resolve()
        self.claim_audit_root = (artifacts_root / "uat-claim-audits" / claim_revision).resolve()
        self.claim_result_root = (artifacts_root / "uat-claim-results" / claim_revision).resolve()
        self.claim_coverage_path = (
            artifacts_root / "uat-claim-audits" / claim_revision / "coverage.json"
        ).resolve()

    @staticmethod
    def _safe_id(value: str) -> str:
        if len(value) != 20 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("UAT_ARTIFACT_ID_INVALID")
        return value

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

    def persist_bundle(self, candidate_id: str, bundle: Mapping[str, Any]) -> dict[str, object]:
        safe_id = self._safe_id(candidate_id)
        payload = (
            json.dumps(dict(bundle), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode()
        path = self.bundle_root / f"{safe_id}.json"
        if path.is_file() and path.read_bytes() != payload:
            raise ValueError("UAT_BUNDLE_IMMUTABLE_MISMATCH")
        if not path.is_file():
            self._atomic_write(path, payload)
        return {
            "candidate_id": safe_id,
            "bundle_ref": f"uat-bundles/{self.bundle_revision}/{safe_id}.json",
            "bundle_sha256": _sha256(payload),
            "bundle_bytes": len(payload),
        }

    def read_bundle(self, candidate_id: str) -> dict[str, Any]:
        safe_id = self._safe_id(candidate_id)
        loaded = json.loads((self.bundle_root / f"{safe_id}.json").read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or loaded.get("candidate_id") != safe_id:
            raise ValueError("UAT_BUNDLE_INVALID")
        return loaded

    def persist_result(self, candidate_id: str, result: Mapping[str, Any]) -> dict[str, object]:
        safe_id = self._safe_id(candidate_id)
        payload = (
            json.dumps(dict(result), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode()
        path = self.result_root / f"{safe_id}.json"
        if path.is_file() and path.read_bytes() != payload:
            raise ValueError("UAT_RESULT_IMMUTABLE_MISMATCH")
        if not path.is_file():
            self._atomic_write(path, payload)
        return {
            "candidate_id": safe_id,
            "result_ref": f"uat-results/{self.result_revision}/{safe_id}.json",
            "result_sha256": _sha256(payload),
            "result_bytes": len(payload),
        }

    def persist_claim_audit_manifest(
        self, test_case_id: str, manifest: Mapping[str, Any]
    ) -> dict[str, object]:
        """Persist a content-free, immutable audit record for a future UAT case."""

        safe_id = self._safe_audit_id(test_case_id)
        record = dict(manifest)
        if (
            record.get("test_case_id") != safe_id
            or record.get("revision") != "uat-audit-manifest:v1"
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
            or loaded.get("revision") != "uat-audit-manifest:v1"
            or loaded.get("test_case_id") != safe_id
        ):
            raise ValueError("UAT_AUDIT_MANIFEST_INVALID")
        return loaded

    def persist_claim_coverage_manifest(self, manifest: Mapping[str, Any]) -> dict[str, object]:
        record = dict(manifest)
        if (
            record.get("revision") != "uat-audit-coverage-manifest:v1"
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
        if (
            not isinstance(loaded, dict)
            or loaded.get("revision") != "uat-audit-coverage-manifest:v1"
        ):
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
            or record.get("revision") != "future-uat-claim-result:v1"
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

    def persist_reranker_failure_review(
        self, review_id: str, review: Mapping[str, Any]
    ) -> dict[str, object]:
        if review_id != "reranker-failure-1":
            raise ValueError("UAT_REVIEW_ID_INVALID")
        payload = (
            json.dumps(dict(review), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode()
        path = self.review_root / f"{review_id}.json"
        if path.is_file() and path.read_bytes() != payload:
            raise ValueError("UAT_REVIEW_IMMUTABLE_MISMATCH")
        if not path.is_file():
            self._atomic_write(path, payload)
        return {
            "review_ref": f"uat-result-review/{review_id}.json",
            "review_sha256": _sha256(payload),
            "review_bytes": len(payload),
        }

    def persist_candidate_revision_proposals(
        self, candidate_number: int, proposals: Mapping[str, Any]
    ) -> dict[str, object]:
        if candidate_number != 2:
            raise ValueError("UAT_REVISION_PROPOSAL_CANDIDATE_INVALID")
        filename = "candidate2-revision-proposals.json"
        payload = (
            json.dumps(dict(proposals), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode()
        path = self.review_root / filename
        if path.is_file() and path.read_bytes() != payload:
            raise ValueError("UAT_REVISION_PROPOSALS_IMMUTABLE_MISMATCH")
        if not path.is_file():
            self._atomic_write(path, payload)
        return {
            "proposal_ref": f"uat-result-review/{filename}",
            "proposal_sha256": _sha256(payload),
            "proposal_bytes": len(payload),
        }

    def persist_candidate_revision_v2(self, revision: Mapping[str, Any]) -> dict[str, object]:
        filename = "candidate2-revision-v2.json"
        payload = (
            json.dumps(dict(revision), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode()
        path = self.review_root / filename
        if path.is_file() and path.read_bytes() != payload:
            raise ValueError("UAT_CANDIDATE_REVISION_V2_IMMUTABLE_MISMATCH")
        if not path.is_file():
            self._atomic_write(path, payload)
        return {
            "revision_ref": f"uat-result-review/{filename}",
            "revision_sha256": _sha256(payload),
            "revision_bytes": len(payload),
        }

    def persist_diagnostic_bundle_v2(
        self, candidate_id: str, bundle: Mapping[str, Any]
    ) -> dict[str, object]:
        safe_id = self._safe_id(candidate_id)
        payload = (
            json.dumps(dict(bundle), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode()
        path = self.diagnostic_bundle_root / f"{safe_id}.json"
        if path.is_file() and path.read_bytes() != payload:
            raise ValueError("UAT_DIAGNOSTIC_BUNDLE_V2_IMMUTABLE_MISMATCH")
        if not path.is_file():
            self._atomic_write(path, payload)
        return {
            "candidate_id": safe_id,
            "bundle_ref": f"uat-diagnostic-bundles/v2/{safe_id}.json",
            "bundle_sha256": _sha256(payload),
            "bundle_bytes": len(payload),
        }

    def read_diagnostic_bundle_v2(self, candidate_id: str) -> dict[str, Any]:
        safe_id = self._safe_id(candidate_id)
        loaded = json.loads(
            (self.diagnostic_bundle_root / f"{safe_id}.json").read_text(encoding="utf-8")
        )
        if not isinstance(loaded, dict) or loaded.get("candidate_id") != safe_id:
            raise ValueError("UAT_DIAGNOSTIC_BUNDLE_V2_INVALID")
        return loaded

    def persist_systematic_revision_v4(
        self,
        review: Mapping[str, Any],
        bundles: Mapping[str, Mapping[str, Any]],
        manifest_base: Mapping[str, Any],
    ) -> dict[str, object]:
        review_payload = (
            json.dumps(dict(review), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode()
        bundle_payloads = {
            self._safe_id(candidate_id): (
                json.dumps(dict(bundle), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                + "\n"
            ).encode()
            for candidate_id, bundle in bundles.items()
        }
        bundle_records = [
            {
                "candidate_id": candidate_id,
                "bundle_ref": f"uat-systematic-revision-v4/bundles/{candidate_id}.json",
                "bundle_sha256": _sha256(payload),
                "bundle_bytes": len(payload),
            }
            for candidate_id, payload in sorted(bundle_payloads.items())
        ]
        manifest = {
            **dict(manifest_base),
            "review_ref": "uat-systematic-revision-v4/approved-review.json",
            "review_sha256": _sha256(review_payload),
            "review_bytes": len(review_payload),
            "bundle_count": len(bundle_records),
            "bundle_records": bundle_records,
            "bundle_snapshot_sha256": _sha256(
                json.dumps(bundle_records, separators=(",", ":"), sort_keys=True).encode()
            ),
            "content_output": False,
        }
        manifest_payload = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode()
        expected = {
            "approved-review.json": review_payload,
            "manifest.json": manifest_payload,
            **{
                f"bundles/{candidate_id}.json": payload
                for candidate_id, payload in bundle_payloads.items()
            },
        }
        target = self.systematic_revision_root
        if target.exists():
            for relative, payload in expected.items():
                path = target / relative
                if not path.is_file() or path.read_bytes() != payload:
                    raise ValueError("UAT_SYSTEMATIC_REVISION_V4_IMMUTABLE_MISMATCH")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(
                tempfile.mkdtemp(prefix=".uat-systematic-v4-", dir=target.parent)
            ).resolve()
            try:
                for relative, payload in expected.items():
                    path = temporary / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payload)
                    path.chmod(0o600)
                os.replace(temporary, target)
            finally:
                if temporary.exists() and temporary.parent == target.parent:
                    shutil.rmtree(temporary)
        return {
            "review_ref": manifest["review_ref"],
            "review_sha256": manifest["review_sha256"],
            "manifest_ref": "uat-systematic-revision-v4/manifest.json",
            "manifest_sha256": _sha256(manifest_payload),
            "bundle_count": len(bundle_records),
            "bundle_snapshot_sha256": manifest["bundle_snapshot_sha256"],
        }

    def persist_systematic_revision_v5(
        self,
        review: Mapping[str, Any],
        bundles: Mapping[str, Mapping[str, Any]],
        manifest_base: Mapping[str, Any],
    ) -> dict[str, object]:
        review_payload = (
            json.dumps(dict(review), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode()
        bundle_payloads = {
            self._safe_id(candidate_id): (
                json.dumps(dict(bundle), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                + "\n"
            ).encode()
            for candidate_id, bundle in bundles.items()
        }
        bundle_records = [
            {
                "candidate_id": candidate_id,
                "bundle_ref": f"uat-systematic-revision-v5/bundles/{candidate_id}.json",
                "bundle_sha256": _sha256(payload),
                "bundle_bytes": len(payload),
            }
            for candidate_id, payload in sorted(bundle_payloads.items())
        ]
        manifest = {
            **dict(manifest_base),
            "review_ref": "uat-systematic-revision-v5/approved-review.json",
            "review_sha256": _sha256(review_payload),
            "review_bytes": len(review_payload),
            "bundle_count": len(bundle_records),
            "bundle_records": bundle_records,
            "bundle_snapshot_sha256": _sha256(
                json.dumps(bundle_records, separators=(",", ":"), sort_keys=True).encode()
            ),
            "content_output": False,
        }
        manifest_payload = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode()
        expected = {
            "approved-review.json": review_payload,
            "manifest.json": manifest_payload,
            **{
                f"bundles/{candidate_id}.json": payload
                for candidate_id, payload in bundle_payloads.items()
            },
        }
        target = self.systematic_revision_v5_root
        if target.exists():
            for relative, payload in expected.items():
                path = target / relative
                if not path.is_file() or path.read_bytes() != payload:
                    raise ValueError("UAT_SYSTEMATIC_REVISION_V5_IMMUTABLE_MISMATCH")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(
                tempfile.mkdtemp(prefix=".uat-systematic-v5-", dir=target.parent)
            ).resolve()
            try:
                for relative, payload in expected.items():
                    path = temporary / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payload)
                    path.chmod(0o600)
                os.replace(temporary, target)
            finally:
                if temporary.exists() and temporary.parent == target.parent:
                    shutil.rmtree(temporary)
        return {
            "review_ref": manifest["review_ref"],
            "review_sha256": manifest["review_sha256"],
            "manifest_ref": "uat-systematic-revision-v5/manifest.json",
            "manifest_sha256": _sha256(manifest_payload),
            "bundle_count": len(bundle_records),
            "bundle_snapshot_sha256": manifest["bundle_snapshot_sha256"],
        }
