"""Diagnose, then explicitly apply evidence-consistent publication state recovery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter  # noqa: E402
from ragkb.adapters.vector_indexing import vector_collection_name  # noqa: E402
from ragkb.adapters.zilliz import MilvusHybridAdapter  # noqa: E402
from ragkb.config import load_env  # noqa: E402
from ragkb.infrastructure.publication_repair import PublicationRepair  # noqa: E402
from ragkb.infrastructure.sqlite import SQLiteDatabase  # noqa: E402
from ragkb.infrastructure.workspace_db import WorkspaceDB  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/redesign/history-repair")
    args = parser.parse_args()
    settings = load_env().settings
    control = MySQLControlPlaneAdapter(settings)
    vector = MilvusHybridAdapter(settings)

    def probe(document_id, version_id, generation):
        condition = " and ".join(
            f"{key} == {json.dumps(value)}"
            for key, value in (
                ("tenant_id", settings.auth_local_tenant),
                ("document_id", document_id),
                ("document_version_id", version_id),
                ("index_generation_id", generation),
            )
        )
        return vector._connected().query(
            collection_name=vector_collection_name(settings),
            filter=condition,
            output_fields=[
                "chunk_id",
                "document_id",
                "document_version_id",
                "index_generation_id",
                "content_checksum",
                "lifecycle_projection",
                "current_version",
                "permission_revision",
                "visibility",
                "acl_scope_tokens",
                "classification_level",
                "valid_from_epoch",
                "valid_to_epoch",
            ],
            limit=16384,
            consistency_level="Strong",
            timeout=30,
        )

    try:
        repair = PublicationRepair(
            WorkspaceDB(SQLiteDatabase(ROOT / "data/storage/control.sqlite3"), control),
            settings.auth_local_tenant,
            probe,
        )
        result = repair.run(args.output, apply=args.apply)
        print(
            json.dumps(
                {
                    "mode": result["mode"],
                    "changed": result["changed"],
                    "decisions": [
                        {
                            k: item.get(k)
                            for k in (
                                "document_id",
                                "filename",
                                "decision",
                                "reasons",
                                "vector_count",
                                "projection_count",
                            )
                        }
                        for item in result["documents"]
                        if item["decision"] != "not_candidate"
                    ],
                    "not_candidates": sum(
                        item["decision"] == "not_candidate" for item in result["documents"]
                    ),
                },
                ensure_ascii=False,
            )
        )
    finally:
        control.close()
        if vector._client is not None:
            vector._client.close()


if __name__ == "__main__":
    main()
