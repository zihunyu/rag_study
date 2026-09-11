"""Preview or explicitly attest an existing generation's embedding configuration.

No vectors are read, generated, modified or deleted. This is a metadata migration,
not a claim that historical vector values were individually revalidated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.embedding_contracts import (  # noqa: E402
    EmbeddingContractRegistry,
    embedding_contract,
    encoded_contract,
    vector_target,
)
from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter  # noqa: E402
from ragkb.config import load_env  # noqa: E402
from ragkb.infrastructure.mysql_migrations import apply_mysql_migrations  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--contract-sha256", default="")
    parser.add_argument("--provenance", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = load_env(ROOT).settings
    if settings is None:
        raise ValueError("CONFIGURATION_INVALID")
    digest = hashlib.sha256(encoded_contract(settings).encode()).hexdigest()
    result = {
        "mode": "apply" if args.apply else "preview",
        "generation_id": args.generation,
        "target_id": vector_target(settings),
        "contract": embedding_contract(settings),
        "contract_sha256": digest,
        "registration_basis": "operator_attested_historical_configuration",
        "historical_vectors_individually_verified": False,
        "vectors_changed": 0,
        "model_calls": 0,
    }
    if args.apply:
        if args.contract_sha256 != digest or not args.provenance.strip():
            raise ValueError("REVIEWED_CONTRACT_DIGEST_AND_PROVENANCE_REQUIRED")
        if len(args.provenance) > 1024:
            raise ValueError("CONTRACT_PROVENANCE_TOO_LONG")
        control = MySQLControlPlaneAdapter(settings)
        try:
            connection = control.connect()
            try:
                result["migrations"] = apply_mysql_migrations(connection)
            finally:
                connection.close()
            registry = EmbeddingContractRegistry(mysql=control)
            registry.bind(settings, args.generation, provenance=args.provenance)
            registry.require(settings, args.generation)
            result["registered"] = True
        finally:
            control.close()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
