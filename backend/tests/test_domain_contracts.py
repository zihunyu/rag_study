from __future__ import annotations

import uuid

import pytest
from ragkb.domain.documents import CanonicalDocument, CanonicalNode, NodeType, SourceLocator
from ragkb.domain.ids import new_uuid7
from ragkb.domain.state_machines import (
    DocumentState,
    JobState,
    PublicationState,
    TransitionError,
    UploadSessionState,
    VersionProcessingState,
    transition_document,
    transition_job,
    transition_processing,
    transition_publication,
    transition_upload,
)


def test_uuid7_is_time_ordered_and_has_expected_version() -> None:
    first = new_uuid7(1_000)
    second = new_uuid7(1_001)

    assert uuid.UUID(first).version == 7
    assert uuid.UUID(second).version == 7
    assert first < second


def test_locator_and_canonical_document_v1() -> None:
    locator = SourceLocator(page=1, bbox=(0, 0, 100, 20), char_range=(0, 5))
    node = CanonicalNode(
        node_id="n1",
        parent_node_id=None,
        node_type=NodeType.PARAGRAPH,
        original_text="hello",
        display_text="hello",
        locator=locator,
    )
    document = CanonicalDocument(
        document_version_id=new_uuid7(),
        language="en",
        source_format="pdf_text",
        nodes=(node,),
        parser_revision="test:v1",
        normalization_revision="normalization:v1",
        content_checksum="a" * 64,
    )

    serialized = document.to_dict()
    assert serialized["contract_version"] == "1.0"
    assert serialized["real_acceptance"] is False
    assert serialized["nodes"][0]["locator"]["page"] == 1


def test_locator_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError):
        SourceLocator(char_range=(5, 2))
    with pytest.raises(ValueError):
        SourceLocator(page=0)
    with pytest.raises(ValueError):
        SourceLocator()


def test_state_machine_happy_paths_and_invalid_transition() -> None:
    assert transition_job(JobState.QUEUED, JobState.RUNNING) is JobState.RUNNING
    assert (
        transition_document(DocumentState.ACTIVE, DocumentState.SWITCHING)
        is DocumentState.SWITCHING
    )
    assert (
        transition_processing(VersionProcessingState.PROCESSING, VersionProcessingState.VALIDATED)
        is VersionProcessingState.VALIDATED
    )
    assert (
        transition_publication(PublicationState.DRAFT, PublicationState.SERVING)
        is PublicationState.SERVING
    )
    assert (
        transition_upload(UploadSessionState.UPLOADED, UploadSessionState.VALIDATED)
        is UploadSessionState.VALIDATED
    )
    with pytest.raises(TransitionError):
        transition_job(JobState.SUCCEEDED, JobState.RUNNING)
