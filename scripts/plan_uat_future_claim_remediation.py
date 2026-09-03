"""Emit the future-only plan for structured-claim UAT submissions."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.application.uat_future_claim_runner import FutureUatClaimRunner  # noqa: E402

OUTPUT = ROOT / "artifacts/final-validation/uat-future-claim-v1-plan.json"


def _atomic_json(path: Path, value: object) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
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
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    plan = {
        "revision": "uat-future-claim-plan:v1",
        "runner_revision": FutureUatClaimRunner.revision,
        "input_contract_revision": "uat-claim-contract:v1",
        "evidence_envelope_revision": "uat-evidence-envelope:v1",
        "audit_manifest_revision": "uat-audit-manifest:v1",
        "source_integrity_required": True,
        "structured_claims_required": True,
        "derived_locator_grounding_required": True,
        "case_input_required_fields": [
            "test_case_id",
            "question",
            "evidence",
            "allow_cross_document",
        ],
        "execution": {
            "checkpoint_ref": "provider-checkpoints/uat-future-claim-v1.json",
            "result_ref": "uat-claim-results/v1",
            "audit_ref": "uat-claim-audits/v1",
            "automatic_retries": 0,
            "approved_by_user": False,
            "executed": False,
            "model_call_count": 0,
            "network_call_count": 0,
        },
        "historical_uat_artifacts": "READ_ONLY",
        "provider_call_count": 0,
        "zilliz_write_count": 0,
        "content_output": False,
    }
    _atomic_json(OUTPUT, plan)
    print(
        json.dumps(
            {
                "revision": plan["revision"],
                "runner_revision": plan["runner_revision"],
                "source_integrity_required": True,
                "structured_claims_required": True,
                "executed": False,
                "provider_call_count": 0,
                "network_call_count": 0,
                "content_output": False,
                "plan_sha256": hashlib.sha256(
                    OUTPUT.read_bytes(), usedforsecurity=False
                ).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
