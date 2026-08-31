"""G0-safe adapters implementing vendor-neutral ports."""

from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.mineru_pool import MinerUTokenPool
from ragkb.adapters.stubs import (
    DeterministicEmbedding,
    DeterministicGeneration,
    DeterministicReranker,
    InMemoryJobQueue,
    StubPermissionProjection,
)
from ragkb.adapters.zilliz import ZillizCloudAdapter

__all__ = [
    "DeterministicEmbedding",
    "DeterministicGeneration",
    "DeterministicReranker",
    "InMemoryJobQueue",
    "LocalFileStorage",
    "MinerUTokenPool",
    "StubPermissionProjection",
    "ZillizCloudAdapter",
]
