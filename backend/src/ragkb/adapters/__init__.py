"""G0-safe adapters implementing vendor-neutral ports."""

from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.mineru_pool import MinerUTokenPool
from ragkb.adapters.model_http import (
    BillableCallApprovalRequired,
    OpenAICompatibleEmbeddingAdapter,
    OpenAICompatibleRerankerAdapter,
)
from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.adapters.rag_stubs import (
    DeterministicBufferedGenerator,
    StaticFinalPermission,
    SyntheticEvidenceProvider,
)
from ragkb.adapters.redis_cache import RedisCacheRateLimitAdapter
from ragkb.adapters.stubs import (
    DeterministicEmbedding,
    DeterministicGeneration,
    DeterministicReranker,
    InMemoryJobQueue,
    StubPermissionProjection,
)
from ragkb.adapters.zilliz import ZillizCloudAdapter, ZillizSafeProjectionWriter

__all__ = [
    "BillableCallApprovalRequired",
    "DeterministicEmbedding",
    "DeterministicBufferedGenerator",
    "DeterministicGeneration",
    "DeterministicReranker",
    "InMemoryJobQueue",
    "LocalFileStorage",
    "MinerUTokenPool",
    "MySQLControlPlaneAdapter",
    "OpenAICompatibleEmbeddingAdapter",
    "OpenAICompatibleRerankerAdapter",
    "RedisCacheRateLimitAdapter",
    "StaticFinalPermission",
    "StubPermissionProjection",
    "SyntheticEvidenceProvider",
    "ZillizCloudAdapter",
    "ZillizSafeProjectionWriter",
]
