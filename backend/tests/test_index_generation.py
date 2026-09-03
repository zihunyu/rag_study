from __future__ import annotations

import pytest
from ragkb.application.indexing import activate_generation, reconcile_index
from ragkb.domain.indexing import IndexGeneration, IndexGenerationState


def _generation(state: IndexGenerationState) -> IndexGeneration:
    return IndexGeneration(
        generation_id="generation-1",
        tenant_id="tenant-1",
        space_id="space-1",
        source_snapshot_seq=20,
        last_applied_event_seq=20,
        security_watermark=12,
        state=state,
    )


def test_generation_state_machine_and_clean_reconciliation_activate() -> None:
    generation = _generation(IndexGenerationState.BUILDING)
    generation = generation.transition(IndexGenerationState.CATCHING_UP)
    generation = generation.transition(IndexGenerationState.VERIFYING)
    generation = generation.transition(IndexGenerationState.READY)
    report = reconcile_index(
        {"chunk-1": "checksum-1"},
        {"chunk-1": "checksum-1"},
        source_snapshot_seq=20,
        last_applied_event_seq=20,
        required_security_watermark=12,
        observed_security_watermark=12,
    )

    assert report.ready is True
    assert activate_generation(generation, report).state is IndexGenerationState.ACTIVE


def test_reconciliation_detects_missing_stale_checksum_lag_and_watermark() -> None:
    report = reconcile_index(
        {"missing": "a", "mismatch": "b"},
        {"stale": "c", "mismatch": "wrong"},
        source_snapshot_seq=20,
        last_applied_event_seq=19,
        required_security_watermark=12,
        observed_security_watermark=11,
    )

    assert report.missing_chunk_ids == ("missing",)
    assert report.stale_chunk_ids == ("stale",)
    assert report.checksum_mismatch_chunk_ids == ("mismatch",)
    assert report.ready is False
    with pytest.raises(ValueError):
        activate_generation(_generation(IndexGenerationState.READY), report)
