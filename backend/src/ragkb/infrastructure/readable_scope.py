"""Prove an empty current reading scope without sending content to a model."""

from ragkb.application.authorization import ResourceAuthorizationService
from ragkb.application.cancellation import check_cancelled
from ragkb.contracts.uploads import UploadRepositoryPort
from ragkb.domain.pagination import PageKey
from ragkb.domain.retrieval import SearchContext


class ReadableScope:
    def __init__(
        self, repository: UploadRepositoryPort, authorization: ResourceAuthorizationService
    ) -> None:
        self.repository, self.authorization = repository, authorization

    def __call__(self, context: SearchContext) -> bool | None:
        # False requires an exhaustive scan; a limit, missing capability or an
        # exception must never be converted to a claim that knowledge is absent.
        scanned = 0
        for space in context.space_ids:
            cursor: PageKey | None = None
            while scanned < 1000:
                check_cancelled()
                page = self.repository.list_documents_page(
                    space, current_only=True, limit=50, after=cursor
                )
                # The repository may return an empty filtered page with a next
                # cursor (e.g. all 50 documents are drafts). Bound scanned pages.
                scanned += 50
                for row in page.items:
                    identity = row["document_id"]
                    if context.document_ids and identity not in context.document_ids:
                        continue
                    self.authorization.lifecycle.reload()
                    record = self.authorization.lifecycle.documents.get(identity)
                    if record and self.authorization.has_readable_chunks(
                        identity,
                        row["version_id"],
                        context,
                        permission_revision=record.acl_revision,
                    ):
                        return True
                if page.next_key is None:
                    break
                cursor = page.next_key
            else:
                return None
        return False
