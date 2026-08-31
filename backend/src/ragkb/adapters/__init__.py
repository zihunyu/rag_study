"""G0-safe adapters implementing vendor-neutral ports."""

from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.mineru import MinerUEndpoint, MinerURouter
from ragkb.adapters.stubs import (
    DeterministicEmbedding,
    DeterministicGeneration,
    DeterministicReranker,
    InMemoryJobQueue,
    StubPermissionProjection,
)

__all__ = [
    "DeterministicEmbedding",
    "DeterministicGeneration",
    "DeterministicReranker",
    "InMemoryJobQueue",
    "LocalFileStorage",
    "MinerUEndpoint",
    "MinerURouter",
    "StubPermissionProjection",
]
