"""Atomically publish the active retrieval generation and security watermark in MySQL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter  # noqa: E402
from ragkb.adapters.mysql_retrieval import MySQLRetrievalControlPlane  # noqa: E402
from ragkb.config import load_env  # noqa: E402
from ragkb.domain.retrieval import RetrievalRelease  # noqa: E402

APPROVAL = "RETRIEVAL_RELEASE_UPDATE_APPROVED"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approval", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--space-id", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--permission-revision", type=int, required=True)
    parser.add_argument("--security-watermark", type=int, required=True)
    args = parser.parse_args()
    if args.approval != APPROVAL:
        raise SystemExit("RETRIEVAL_RELEASE_UPDATE_APPROVAL_REQUIRED")
    settings = load_env(ROOT).settings
    if settings is None:
        raise SystemExit("CONFIG_INVALID")
    adapter = MySQLRetrievalControlPlane(MySQLControlPlaneAdapter(settings))
    release = RetrievalRelease(
        args.tenant_id,
        args.space_id,
        args.generation_id,
        args.permission_revision,
        args.security_watermark,
    )
    adapter.set_release(release)
    confirmed = adapter.current_release(args.tenant_id, args.space_id)
    if confirmed != release:
        raise SystemExit("RETRIEVAL_RELEASE_CONFIRMATION_FAILED")
    print(
        json.dumps(
            {
                "status": "RETRIEVAL_RELEASE_CONFIRMED",
                "generation_id": confirmed.active_generation_id,
                "permission_revision": confirmed.active_permission_revision,
                "security_watermark": confirmed.security_watermark,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
