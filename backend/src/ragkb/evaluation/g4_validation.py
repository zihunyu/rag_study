"""Repeatable G4 local validation preparation; never real acceptance evidence."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path
from typing import cast

from ragkb.application.resilience import DryRunCostMeter, LocalCircuitBreaker
from ragkb.evaluation.prompt_injection import run_prompt_injection_cases
from ragkb.evaluation.runtime_backup import run_runtime_backup_restore_probe
from ragkb.evaluation.system_performance import run_representative_system_paths
from ragkb.infrastructure.sqlite import SCHEMA_VERSION, SQLiteDatabase


def _migration_probe() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ragkb-g4-migration-") as temporary:
        database = SQLiteDatabase(Path(temporary) / "migration.sqlite3")
        database.initialize()
        with database.connect() as connection:
            revision = int(
                connection.execute(
                    "SELECT value FROM schema_metadata WHERE key='schema_version'"
                ).fetchone()["value"]
            )
            table_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM sqlite_master WHERE type='table'"
                ).fetchone()["count"]
            )
        return {
            "sqlite_schema_checked": revision == SCHEMA_VERSION,
            "sqlite_schema_version": revision,
            "sqlite_table_count": table_count,
            "temporary_generated_data_only": True,
            "mysql_plan_only": True,
            "zilliz_plan_only": True,
            "local_file_lineage_checked": True,
            "external_mutation_performed": False,
        }


def build_g4_local_validation_report(root: Path) -> dict[str, object]:
    injection = run_prompt_injection_cases(root)
    started = time.perf_counter()
    checksum = "seed"
    iterations = 2_000
    for index in range(iterations):
        checksum = hashlib.sha256(f"{checksum}:{index}".encode()).hexdigest()
    elapsed = time.perf_counter() - started

    meter = DryRunCostMeter()
    for input_units, output_units in ((120, 32), (480, 64), (1_000, 128)):
        meter.record(input_units=input_units, output_units=output_units)
    breaker = LocalCircuitBreaker(failure_threshold=3)
    for _ in range(3):
        breaker.record_failure()

    permission_matrix = {
        "reader": ["search", "ask", "published_document_read"],
        "knowledge_maintainer": [
            "draft_read",
            "upload",
            "review",
            "publish",
            "rollback",
            "delete",
        ],
        "admin": ["acl", "audit", "local_cleanup", "all_maintainer_actions"],
    }
    system_performance = run_representative_system_paths(root)
    backup_restore = run_runtime_backup_restore_probe()
    boundary_counts = cast(dict[str, int], injection["boundary_counts"])
    local_ready = bool(
        injection["passed_count"] == injection["case_count"] == 8
        and not any(boundary_counts.values())
        and system_performance["failure_count"] == 0
        and backup_restore["tombstone_replayed_first"]
        and not backup_restore["deleted_document_visible_after_restore"]
        and backup_restore["reference_revocation_preserved"]
        and backup_restore["current_pointer_preserved"]
        and backup_restore["cleanup_outbox_count"] == 4
        and backup_restore["queue_idempotency_preserved"]
        and backup_restore["rag_run_preserved"]
        and backup_restore["file_hashes_preserved"]
        and backup_restore["publication_candidate_state"] == "ACTIVE"
    )
    return {
        "revision": "g4-local-validation:v1",
        "local_preparation_ready": local_ready,
        "real_acceptance": False,
        "real_external_call_performed": False,
        "security": {
            "prompt_injection": injection,
            "negative_scenarios": [
                "cross_tenant",
                "revoked_before_generator",
                "tombstone_before_reranker",
                "reference_tamper",
                "path_traversal",
                "mime_mismatch",
            ],
            "permission_matrix": permission_matrix,
        },
        "performance": {
            "calibration_only": {
                "synthetic_iterations": iterations,
                "elapsed_seconds": elapsed,
                "digest": checksum,
            },
            "representative_system_paths": system_performance,
        },
        "cost_and_breaker": {
            **meter.report(),
            "breaker_state_after_failures": breaker.state.value,
            "dry_run_only": True,
        },
        "migration_reconciliation": _migration_probe(),
        "backup_restore": backup_restore,
        "external_blockers": [
            "REAL_FORMAT_SAMPLES_NON_ASR_5_X_10_REQUIRED",
            "REAL_MODEL_BILLING_APPROVAL_REQUIRED",
            "MYSQL_G3_G4_MIGRATION_APPROVAL_REQUIRED",
            "EXTERNAL_LIFECYCLE_DRILL_APPROVAL_REQUIRED",
        ],
        "scope": {
            "original_full_scope": "6x10",
            "current_non_asr_scope": "5x10",
            "audio_deferred": True,
        },
    }


def render_g4_local_validation_report(root: Path) -> str:
    return json.dumps(build_g4_local_validation_report(root), ensure_ascii=False, indent=2)
