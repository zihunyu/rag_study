"""Run one bounded MySQL/Redis/Zilliz revoke-republish-delete lifecycle drill."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter  # noqa: E402
from ragkb.adapters.mysql_retrieval import MySQLRetrievalControlPlane  # noqa: E402
from ragkb.adapters.redis_cache import RedisCacheRateLimitAdapter  # noqa: E402
from ragkb.adapters.redis_queue import RedisPersistentJobQueue  # noqa: E402
from ragkb.adapters.vector_indexing import (  # noqa: E402
    ZillizSafeProjectionWriter,
    vector_analyzer,
    vector_dense_field,
)
from ragkb.adapters.zilliz import ZillizCloudAdapter  # noqa: E402
from ragkb.config import build_env_report, load_env  # noqa: E402
from ragkb.domain.retrieval import (  # noqa: E402
    AuthorizedChunk,
    RetrievalRelease,
    SearchContext,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/acceptance/lifecycle-drill.json")
    )
    args = parser.parse_args()
    if not args.approved:
        print("status=EXTERNAL_LIFECYCLE_DRILL_APPROVAL_REQUIRED")
        return 2
    loaded = load_env(ROOT)
    gate = build_env_report(loaded, "G4")
    if loaded.settings is None or not gate["summary"]["gate_ready"]:  # type: ignore[index]
        print(json.dumps({"status": "G4_CONFIG_NOT_READY", "blockers": gate["gate_blockers"]}))
        return 3
    settings = loaded.settings
    suffix = hashlib.sha256(
        f"{os.environ.get('GITHUB_RUN_ID', 'local')}:{time.time_ns()}".encode()
    ).hexdigest()[:16]
    tenant = f"drill-tenant-{suffix}"
    space = f"drill-space-{suffix}"
    document = f"drill-document-{suffix}"
    version = f"drill-version-{suffix}"
    chunk = f"drill-chunk-{suffix}"
    generation = f"drill-generation-{suffix}"
    scope = f"group:drill-reader-{suffix}"
    control_adapter = MySQLControlPlaneAdapter(settings)
    mysql = MySQLRetrievalControlPlane(control_adapter)
    watermark = [1]
    vector = ZillizCloudAdapter(settings, watermark_provider=lambda _: watermark[0])
    redis_adapter = RedisCacheRateLimitAdapter(settings)
    queue = RedisPersistentJobQueue(redis_adapter)
    vector_record = {
        "zilliz_pk": f"{tenant}:{generation}:{chunk}",
        "tenant_id": tenant,
        "space_id": space,
        "corpus_id": "lifecycle-drill",
        "document_id": document,
        "document_version_id": version,
        "chunk_id": chunk,
        "parent_chunk_id": "",
        "chunk_type": "paragraph",
        "language": "en",
        "valid_from_epoch": 0,
        "valid_to_epoch": 0,
        "lifecycle_projection": "SERVING",
        "current_version": True,
        "visibility": "RESTRICTED",
        "acl_scope_tokens": [scope],
        "permission_revision": 1,
        "classification_level": 1,
        "authority_rank": 1,
        "category_ids": [],
        "tag_ids": [],
        "product_ids": [],
        "applicable_versions": [],
        "region_codes": [],
        "retrieval_text": "low cost lifecycle verification token",
        vector_dense_field(settings): [1.0] + [0.0] * (settings.embedding_dimension - 1),
        "index_generation_id": generation,
        "analyzer_revision": vector_analyzer(settings),
        "content_checksum": hashlib.sha256(b"lifecycle drill").hexdigest(),
    }
    projection = AuthorizedChunk(
        chunk,
        tenant,
        space,
        document,
        version,
        None,
        "low cost lifecycle verification token",
        "low cost lifecycle verification token",
        {"page": 1},
        str(vector_record["content_checksum"]),
        "RESTRICTED",
        (scope,),
        1,
        "SERVING",
        0,
        0,
        1,
        True,
    )
    context = SearchContext(
        tenant,
        (space,),
        (scope,),
        1,
        int(time.time()),
        generation,
        1,
        1,
    )
    checks: dict[str, bool] = {}
    cleanup = {"zilliz": False, "mysql": False, "redis": False}
    failure: str | None = None
    try:
        ZillizSafeProjectionWriter(vector._connected(), settings).insert_records([vector_record])
        mysql.upsert_chunks((projection,))
        mysql.set_release(RetrievalRelease(tenant, space, generation, 1, 1))
        checks["initial_visible"] = bool(
            vector.search_dense(vector_record[vector_dense_field(settings)], context, 1)
        )

        vector.set_document_projection(
            document,
            active_version_id=None,
            lifecycle_projection="REVOKED",
            permission_revision=2,
        )
        mysql.set_document_projection(
            document,
            active_version_id=None,
            lifecycle_projection="REVOKED",
            permission_revision=2,
        )
        watermark[0] = 2
        mysql.set_release(RetrievalRelease(tenant, space, generation, 2, 2))
        revoked_context = SearchContext(
            tenant, (space,), (scope,), 1, int(time.time()), generation, 2, 2
        )
        checks["revoked_hidden"] = not bool(
            vector.search_dense(vector_record[vector_dense_field(settings)], revoked_context, 1)
        )

        vector.set_document_projection(
            document,
            active_version_id=version,
            lifecycle_projection="SERVING",
            permission_revision=3,
        )
        mysql.set_document_projection(
            document,
            active_version_id=version,
            lifecycle_projection="SERVING",
            permission_revision=3,
        )
        watermark[0] = 3
        mysql.set_release(RetrievalRelease(tenant, space, generation, 3, 3))
        republished_context = SearchContext(
            tenant, (space,), (scope,), 1, int(time.time()), generation, 3, 3
        )
        checks["republished_visible"] = bool(
            vector.search_dense(vector_record[vector_dense_field(settings)], republished_context, 1)
        )

        queued = queue.enqueue(
            "lifecycle_drill", {"document_id": document}, suffix, suffix, max_attempts=1
        )
        leased = queue.lease(f"drill-worker-{suffix}")
        checks["redis_queue_shared"] = bool(leased and leased.id == queued.id)
        if leased is not None:
            queue.complete(leased.id, f"drill-worker-{suffix}")
    except Exception as error:
        failure = type(error).__name__
    finally:
        try:
            vector.delete_document_projection(document)
            cleanup["zilliz"] = not vector.document_projection_exists(document)
        except Exception:
            cleanup["zilliz"] = False
        try:
            mysql.delete_document_projection(document)
            connection = control_adapter.connect()
            cursor = connection.cursor()
            cursor.execute(
                "DELETE FROM retrieval_release_state WHERE tenant_id=%s AND space_id=%s",
                (tenant, space),
            )
            connection.commit()
            connection.close()
            cleanup["mysql"] = not mysql.document_projection_exists(document)
        except Exception:
            cleanup["mysql"] = False
        try:
            client = redis_adapter._connected()
            client.delete(queue.jobs_key, queue.idempotency_key, queue.lock_key)
            cleanup["redis"] = not bool(client.exists(queue.jobs_key))
        except Exception:
            cleanup["redis"] = False
    passed = failure is None and all(checks.values()) and all(cleanup.values())
    report = {
        "revision": "external-lifecycle-drill:v1",
        "checks": checks,
        "cleanup": cleanup,
        "failure_type": failure,
        "provider_model_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "passed": passed,
        "secret_values_output": False,
    }
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if passed else 4


if __name__ == "__main__":
    raise SystemExit(main())
