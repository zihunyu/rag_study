"""Approve the frozen 78-candidate UAT snapshot without model calls."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.evaluation.uat_candidates import (  # noqa: E402
    approve_all_uat_candidates,
    validate_uat_approval,
)

UAT_ROOT = ROOT / "artifacts/final-validation/uat-candidates"
PENDING = UAT_ROOT / "pending-review.json"
APPROVED = UAT_ROOT / "approved.json"
MANIFEST = UAT_ROOT / "approval-manifest.json"


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


def main() -> int:
    parser = argparse.ArgumentParser()
    decision = parser.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve-all", action="store_true")
    decision.add_argument("--validate", action="store_true")
    parser.add_argument("--expected-hash", required=True)
    args = parser.parse_args()
    pending_before = PENDING.read_bytes()
    if args.validate:
        report = validate_uat_approval(
            pending_before,
            APPROVED.read_bytes(),
            MANIFEST.read_bytes(),
            args.expected_hash,
        )
        print(json.dumps(report, sort_keys=True))
        return 0
    approved, manifest = approve_all_uat_candidates(pending_before, args.expected_hash)
    approved_payload = (
        json.dumps(approved, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    _atomic_write(APPROVED, approved_payload)
    _atomic_write(MANIFEST, manifest_payload)
    if PENDING.read_bytes() != pending_before:
        raise RuntimeError("UAT_PENDING_MUTATED")
    print(
        json.dumps(
            {
                "decision": manifest["decision"],
                "candidate_count": manifest["candidate_count"],
                "pending_sha256": manifest["pending_sha256"],
                "approved_sha256": manifest["approved_sha256"],
                "approved_ids_hash": manifest["approved_ids_hash"],
                "pending_snapshot_unchanged": True,
                "question_text_output": False,
                "network_call_count": 0,
                "reranker_call_count": 0,
                "llm_call_count": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
