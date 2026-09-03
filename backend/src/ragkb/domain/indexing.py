"""G2 index-generation state and reconciliation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IndexGenerationState(StrEnum):
    BUILDING = "BUILDING"
    CATCHING_UP = "CATCHING_UP"
    VERIFYING = "VERIFYING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    FAILED = "FAILED"


INDEX_GENERATION_TRANSITIONS = {
    IndexGenerationState.BUILDING: {
        IndexGenerationState.CATCHING_UP,
        IndexGenerationState.FAILED,
    },
    IndexGenerationState.CATCHING_UP: {
        IndexGenerationState.VERIFYING,
        IndexGenerationState.FAILED,
    },
    IndexGenerationState.VERIFYING: {
        IndexGenerationState.READY,
        IndexGenerationState.FAILED,
    },
    IndexGenerationState.READY: {IndexGenerationState.ACTIVE, IndexGenerationState.FAILED},
    IndexGenerationState.ACTIVE: {IndexGenerationState.RETIRED},
    IndexGenerationState.RETIRED: set(),
    IndexGenerationState.FAILED: set(),
}


@dataclass(frozen=True)
class IndexGeneration:
    generation_id: str
    tenant_id: str
    space_id: str
    source_snapshot_seq: int
    last_applied_event_seq: int
    security_watermark: int
    state: IndexGenerationState

    def transition(self, target: IndexGenerationState) -> IndexGeneration:
        if target not in INDEX_GENERATION_TRANSITIONS[self.state]:
            raise ValueError(f"invalid index generation transition: {self.state} -> {target}")
        return IndexGeneration(
            generation_id=self.generation_id,
            tenant_id=self.tenant_id,
            space_id=self.space_id,
            source_snapshot_seq=self.source_snapshot_seq,
            last_applied_event_seq=self.last_applied_event_seq,
            security_watermark=self.security_watermark,
            state=target,
        )


@dataclass(frozen=True)
class IndexReconciliationReport:
    missing_chunk_ids: tuple[str, ...]
    stale_chunk_ids: tuple[str, ...]
    checksum_mismatch_chunk_ids: tuple[str, ...]
    expected_count: int
    observed_count: int
    source_snapshot_seq: int
    last_applied_event_seq: int
    required_security_watermark: int
    observed_security_watermark: int

    @property
    def ready(self) -> bool:
        return (
            not self.missing_chunk_ids
            and not self.stale_chunk_ids
            and not self.checksum_mismatch_chunk_ids
            and self.last_applied_event_seq >= self.source_snapshot_seq
            and self.observed_security_watermark >= self.required_security_watermark
        )
