from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4


class ApplicationState(StrEnum):
    DRAFT = "DRAFT"
    DOCUMENTS_UPLOADED = "DOCUMENTS_UPLOADED"
    PROCESSING = "PROCESSING"
    ANALYSIS_READY = "ANALYSIS_READY"
    UNDER_REVIEW = "UNDER_REVIEW"
    DECIDED = "DECIDED"
    PROCESSING_FAILED = "PROCESSING_FAILED"


_ALLOWED_TRANSITIONS: dict[ApplicationState, frozenset[ApplicationState]] = {
    ApplicationState.DRAFT: frozenset({ApplicationState.DOCUMENTS_UPLOADED}),
    ApplicationState.DOCUMENTS_UPLOADED: frozenset({ApplicationState.PROCESSING}),
    ApplicationState.PROCESSING: frozenset({ApplicationState.ANALYSIS_READY, ApplicationState.PROCESSING_FAILED}),
    ApplicationState.ANALYSIS_READY: frozenset({ApplicationState.UNDER_REVIEW}),
    ApplicationState.UNDER_REVIEW: frozenset({ApplicationState.DECIDED}),
    ApplicationState.DECIDED: frozenset(),
    ApplicationState.PROCESSING_FAILED: frozenset({ApplicationState.PROCESSING}),
}


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    application_id: str
    event_type: str
    detail: str
    created_at: str


@dataclass(frozen=True, slots=True)
class DocumentMetadata:
    document_id: str
    filename: str
    content_type: str
    size_bytes: int
    uploaded_at: str


@dataclass(slots=True)
class Application:
    application_id: str
    applicant_name: str
    state: ApplicationState = ApplicationState.DRAFT
    documents: list[DocumentMetadata] = field(default_factory=list)


class ApplicationStore:
    def __init__(self) -> None:
        self._applications: dict[str, Application] = {}
        self._audit: dict[str, list[AuditEvent]] = {}

    def create(self, applicant_name: str) -> Application:
        application = Application(str(uuid4()), applicant_name.strip())
        self._applications[application.application_id] = application
        self._audit[application.application_id] = []
        self._record(application.application_id, "application.created", "Application created")
        return application

    def get(self, application_id: str) -> Application | None:
        return self._applications.get(application_id)

    def add_document(self, application_id: str, document: DocumentMetadata) -> Application:
        application = self._require(application_id)
        application.documents.append(document)
        if application.state == ApplicationState.DRAFT:
            self.transition(application_id, ApplicationState.DOCUMENTS_UPLOADED)
        self._record(application_id, "document.registered", document.filename)
        return application

    def transition(self, application_id: str, target: ApplicationState) -> Application:
        application = self._require(application_id)
        allowed = _ALLOWED_TRANSITIONS[application.state]
        if target not in allowed:
            raise ValueError(f"cannot transition from {application.state} to {target}")
        previous = application.state
        application.state = target
        self._record(application_id, "application.state_changed", f"{previous} -> {target}")
        return application

    def audit(self, application_id: str) -> list[AuditEvent]:
        self._require(application_id)
        return list(self._audit[application_id])

    def _require(self, application_id: str) -> Application:
        application = self.get(application_id)
        if application is None:
            raise KeyError(application_id)
        return application

    def _record(self, application_id: str, event_type: str, detail: str) -> None:
        self._audit[application_id].append(AuditEvent(str(uuid4()), application_id, event_type, detail, datetime.now(timezone.utc).isoformat()))


def application_dict(application: Application) -> dict:
    value = asdict(application)
    value["state"] = application.state.value
    return value
