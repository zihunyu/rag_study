"""Find question-bearing documents before spending the chapter-reading allowance."""

from ragkb.application.search import HybridSearchService
from ragkb.contracts.rag import EvidenceSelectorPort
from ragkb.domain.rag import Evidence
from ragkb.domain.retrieval import SearchContext


def search_documents(
    search: HybridSearchService,
    selector: EvidenceSelectorPort | None,
    question: str,
    context: SearchContext,
) -> tuple[str, ...]:
    result = search.search(question, context, limit=20, query_limit=1)
    candidates = tuple(
        Evidence(
            evidence_id=f"E{i}",
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            document_version_id=hit.document_version_id,
            text=hit.text,
            locator=hit.locator,
            valid_from_epoch=hit.valid_from_epoch,
            valid_to_epoch=hit.valid_to_epoch,
            permission_revision=hit.permission_revision,
            authority_rank=0,
            authorized=True,
            current_version=hit.current_version,
        )
        for i, hit in enumerate(result.hits, 1)
    )
    if selector is None:
        return tuple(dict.fromkeys(hit.document_id for hit in result.hits))
    if not candidates:
        return ()
    selection = selector.select(question, candidates)
    if selection.coverage == "ambiguous":
        return ()
    selected = set(selection.source_ids)
    return tuple(dict.fromkeys(e.document_id for e in candidates if e.evidence_id in selected))
