"""Framework- and vendor-independent G1 domain model."""

from ragkb.domain.documents import CanonicalDocument, CanonicalNode, NodeType, SourceLocator
from ragkb.domain.entities import (
    Chunk,
    Corpus,
    Document,
    DocumentVersion,
    KnowledgeSpace,
    Section,
    Source,
    Tenant,
)
from ragkb.domain.ids import new_uuid7
from ragkb.domain.indexing import IndexGeneration, IndexGenerationState, IndexReconciliationReport
from ragkb.domain.lifecycle import LifecycleRecord, LifecycleState, SecurityTransition
from ragkb.domain.rag import AnswerStatus, AskResult, Evidence, EvidencePackage
from ragkb.domain.retrieval import SearchContext, SearchHit, SearchResult
from ragkb.domain.state_machines import (
    DocumentState,
    JobState,
    PublicationState,
    TransitionError,
    UploadSessionState,
    VersionProcessingState,
)

__all__ = [
    "CanonicalDocument",
    "CanonicalNode",
    "AnswerStatus",
    "AskResult",
    "Chunk",
    "Corpus",
    "Document",
    "DocumentState",
    "DocumentVersion",
    "IndexGeneration",
    "IndexGenerationState",
    "IndexReconciliationReport",
    "Evidence",
    "EvidencePackage",
    "JobState",
    "KnowledgeSpace",
    "LifecycleRecord",
    "LifecycleState",
    "NodeType",
    "PublicationState",
    "Section",
    "SearchContext",
    "SearchHit",
    "SearchResult",
    "SecurityTransition",
    "Source",
    "SourceLocator",
    "Tenant",
    "TransitionError",
    "UploadSessionState",
    "VersionProcessingState",
    "new_uuid7",
]
