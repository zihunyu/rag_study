"""Pure G2 index reconciliation and activation checks."""

from __future__ import annotations

from collections.abc import Mapping

from ragkb.domain.indexing import IndexGeneration, IndexGenerationState, IndexReconciliationReport


def reconcile_index(
    expected_checksums: Mapping[str, str],
    observed_checksums: Mapping[str, str],
    *,
    source_snapshot_seq: int,
    last_applied_event_seq: int,
    required_security_watermark: int,
    observed_security_watermark: int,
) -> IndexReconciliationReport:
    expected_ids = set(expected_checksums)
    observed_ids = set(observed_checksums)
    return IndexReconciliationReport(
        missing_chunk_ids=tuple(sorted(expected_ids - observed_ids)),
        stale_chunk_ids=tuple(sorted(observed_ids - expected_ids)),
        checksum_mismatch_chunk_ids=tuple(
            sorted(
                chunk_id
                for chunk_id in expected_ids.intersection(observed_ids)
                if expected_checksums[chunk_id] != observed_checksums[chunk_id]
            )
        ),
        expected_count=len(expected_ids),
        observed_count=len(observed_ids),
        source_snapshot_seq=source_snapshot_seq,
        last_applied_event_seq=last_applied_event_seq,
        required_security_watermark=required_security_watermark,
        observed_security_watermark=observed_security_watermark,
    )


def activate_generation(
    generation: IndexGeneration, report: IndexReconciliationReport
) -> IndexGeneration:
    if generation.state is not IndexGenerationState.READY:
        raise ValueError("only a READY generation can be activated")
    if not report.ready:
        raise ValueError("index reconciliation and security watermark must be ready")
    return generation.transition(IndexGenerationState.ACTIVE)
