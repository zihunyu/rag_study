"""Composition helpers for fail-closed document lifecycle projections."""

from __future__ import annotations

from collections.abc import Sequence

from ragkb.contracts.ports import DocumentProjectionPort


class CompositeDocumentProjection:
    revision = "composite-document-projection:v1"

    def __init__(self, projections: Sequence[DocumentProjectionPort]) -> None:
        if not projections:
            raise ValueError("at least one document projection is required")
        self.projections = tuple(projections)

    def set_document_projection(
        self,
        document_id: str,
        *,
        active_version_id: str | None,
        lifecycle_projection: str,
        permission_revision: int,
    ) -> None:
        for projection in self.projections:
            projection.set_document_projection(
                document_id,
                active_version_id=active_version_id,
                lifecycle_projection=lifecycle_projection,
                permission_revision=permission_revision,
            )

    def delete_document_projection(self, document_id: str) -> None:
        for projection in self.projections:
            projection.delete_document_projection(document_id)
