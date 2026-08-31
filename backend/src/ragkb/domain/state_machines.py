"""Explicit G1 job, document and version state machines."""

from __future__ import annotations

from enum import StrEnum


class TransitionError(ValueError):
    pass


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    SUCCEEDED = "SUCCEEDED"
    FAILED_FINAL = "FAILED_FINAL"


class DocumentState(StrEnum):
    ACTIVE = "ACTIVE"
    SWITCHING = "SWITCHING"
    REVOKING = "REVOKING"
    REVOKED = "REVOKED"
    PURGING = "PURGING"
    DELETED = "DELETED"


class VersionProcessingState(StrEnum):
    DRAFT = "DRAFT"
    PROCESSING = "PROCESSING"
    VALIDATED = "VALIDATED"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"


class PublicationState(StrEnum):
    DRAFT = "DRAFT"
    SERVING = "SERVING"
    SUPERSEDED = "SUPERSEDED"
    RETIRED = "RETIRED"


class UploadSessionState(StrEnum):
    CREATED = "CREATED"
    UPLOADED = "UPLOADED"
    VALIDATED = "VALIDATED"
    PROMOTED = "PROMOTED"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


JOB_TRANSITIONS = {
    JobState.QUEUED: {JobState.RUNNING, JobState.CANCELLED},
    JobState.RUNNING: {
        JobState.SUCCEEDED,
        JobState.RETRY_WAIT,
        JobState.CANCEL_REQUESTED,
        JobState.FAILED_FINAL,
    },
    JobState.RETRY_WAIT: {JobState.QUEUED, JobState.CANCELLED},
    JobState.CANCEL_REQUESTED: {JobState.CANCELLED, JobState.RETRY_WAIT},
    JobState.FAILED_FINAL: {JobState.QUEUED},
    JobState.CANCELLED: set(),
    JobState.SUCCEEDED: set(),
}

DOCUMENT_TRANSITIONS = {
    DocumentState.ACTIVE: {DocumentState.SWITCHING, DocumentState.REVOKING},
    DocumentState.SWITCHING: {DocumentState.ACTIVE},
    DocumentState.REVOKING: {DocumentState.REVOKED, DocumentState.ACTIVE},
    DocumentState.REVOKED: {DocumentState.PURGING, DocumentState.ACTIVE},
    DocumentState.PURGING: {DocumentState.DELETED},
    DocumentState.DELETED: set(),
}

PROCESSING_TRANSITIONS = {
    VersionProcessingState.DRAFT: {VersionProcessingState.PROCESSING},
    VersionProcessingState.PROCESSING: {
        VersionProcessingState.VALIDATED,
        VersionProcessingState.QUARANTINED,
        VersionProcessingState.FAILED,
    },
    VersionProcessingState.QUARANTINED: {VersionProcessingState.PROCESSING},
    VersionProcessingState.FAILED: {VersionProcessingState.PROCESSING},
    VersionProcessingState.VALIDATED: set(),
}

PUBLICATION_TRANSITIONS = {
    PublicationState.DRAFT: {PublicationState.SERVING, PublicationState.RETIRED},
    PublicationState.SERVING: {PublicationState.SUPERSEDED, PublicationState.RETIRED},
    PublicationState.SUPERSEDED: {PublicationState.SERVING, PublicationState.RETIRED},
    PublicationState.RETIRED: set(),
}

UPLOAD_TRANSITIONS = {
    UploadSessionState.CREATED: {UploadSessionState.UPLOADED, UploadSessionState.ABORTED},
    UploadSessionState.UPLOADED: {
        UploadSessionState.VALIDATED,
        UploadSessionState.FAILED,
        UploadSessionState.ABORTED,
    },
    UploadSessionState.VALIDATED: {
        UploadSessionState.PROMOTED,
        UploadSessionState.FAILED,
    },
    UploadSessionState.PROMOTED: {
        UploadSessionState.COMPLETED,
        UploadSessionState.FAILED,
    },
    UploadSessionState.FAILED: {UploadSessionState.UPLOADED, UploadSessionState.ABORTED},
    UploadSessionState.COMPLETED: set(),
    UploadSessionState.ABORTED: set(),
}


def ensure_transition[StateT: StrEnum](
    current: StateT, target: StateT, transitions: dict[StateT, set[StateT]]
) -> StateT:
    if target not in transitions[current]:
        raise TransitionError(f"invalid transition: {current.value} -> {target.value}")
    return target


def transition_job(current: JobState, target: JobState) -> JobState:
    return ensure_transition(current, target, JOB_TRANSITIONS)


def transition_document(current: DocumentState, target: DocumentState) -> DocumentState:
    return ensure_transition(current, target, DOCUMENT_TRANSITIONS)


def transition_processing(
    current: VersionProcessingState, target: VersionProcessingState
) -> VersionProcessingState:
    return ensure_transition(current, target, PROCESSING_TRANSITIONS)


def transition_publication(current: PublicationState, target: PublicationState) -> PublicationState:
    return ensure_transition(current, target, PUBLICATION_TRANSITIONS)


def transition_upload(
    current: UploadSessionState, target: UploadSessionState
) -> UploadSessionState:
    return ensure_transition(current, target, UPLOAD_TRANSITIONS)
