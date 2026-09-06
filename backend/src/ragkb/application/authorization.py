"""Resource authorization shared by retrieval, direct reads and citation rechecks."""

from collections.abc import Mapping, Sequence

from ragkb.application.lifecycle import InMemoryLifecycleStore
from ragkb.contracts.ports import RetrievalControlPlanePort
from ragkb.domain.retrieval import AuthorizedChunk, SearchContext


class ResourceAuthorizationService:
    revision = "resource-authorization"

    def __init__(
        self, control_plane: RetrievalControlPlanePort, lifecycle: InMemoryLifecycleStore
    ) -> None:
        self.control_plane = control_plane
        self.lifecycle = lifecycle

    def authorize_chunks(
        self, chunk_ids: Sequence[str], context: SearchContext
    ) -> Mapping[str, AuthorizedChunk]:
        projected = self.control_plane.authorize_chunks(chunk_ids, context)
        # Recheck the authoritative lifecycle after loading projections. A retained
        # projection must not grant access after revocation or a version/ACL switch.
        self.lifecycle.reload()
        return {
            chunk_id: chunk
            for chunk_id in chunk_ids
            if (chunk := projected.get(chunk_id)) is not None
            and self.lifecycle.authorizes_chunk(chunk, context)
        }

    def authorize_parent(
        self, parent_chunk_id: str, context: SearchContext
    ) -> AuthorizedChunk | None:
        return self.authorize_chunks((parent_chunk_id,), context).get(parent_chunk_id)

    def has_readable_chunks(
        self, document_id: str, version_id: str, context: SearchContext, *, permission_revision: int
    ) -> bool:
        allowed = self.control_plane.has_readable_chunks(
            document_id, version_id, context, permission_revision=permission_revision
        )
        # Refresh after the projection query, so a concurrent revoke/version/ACL
        # switch cannot turn a retained projection into document access.
        self.lifecycle.reload()
        record = self.lifecycle.documents.get(document_id)
        return bool(
            allowed
            and record
            and self.lifecycle.is_accessible(document_id)
            and not self.lifecycle.is_tombstoned(document_id)
            and record.active_version_id == version_id
            and record.acl_revision == permission_revision
        )
