from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import Role, TaskPriority, TaskStatus


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(OrmModel):
    id: str
    username: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    full_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=8, max_length=128)
    role: Role


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: Role | None = None
    is_active: bool | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class DocumentOut(OrmModel):
    id: str
    owner_id: str
    filename: str
    media_type: str
    size_bytes: int
    category: str
    is_confidential: bool
    created_at: datetime


class SourceOut(BaseModel):
    citation_number: int = Field(ge=1)
    document_id: str
    document_name: str
    article_or_clause: str | None
    display_label: str | None = None
    url: str
    excerpt: str
    full_excerpt: str | None = None
    page: int | None = None
    section: str | None = None
    evidence_type: Literal["nhh", "document"] = "document"
    document_type: str | None = None
    official_number: str | None = None


class AiResponse(BaseModel):
    history_id: str
    answer: str
    sources: list[SourceOut] = []
    export_url: str | None = None
    official_export_url: str | None = None
    internal_evidence_url: str | None = None
    export_missing_fields: list[str] = []
    result_kind: Literal["ok", "source_matches", "corpus_empty", "no_sources"] = "ok"
    warning: str | None = None
    effective_mode: Literal["legal", "general"] = "general"
    routed_to_legal: bool = False
    evidence_context: Literal["legal", "document", "draft", "analytics", "general"] = "general"
    structured: dict | None = None


class LlmProviderStatusOut(BaseModel):
    """Administrator-only provider diagnostics.

    Deliberately carries no base URL and no credential: state, model name and a
    safe message are enough to diagnose, and this must never leak an internal
    address even to an administrator's browser.
    """

    name: str
    label: str
    role: Literal["primary", "fallback"]
    state: Literal["not_configured", "unreachable", "unauthorized", "model_missing", "ready"]
    model: str | None = None
    detail: str = ""
    reachable: bool = False
    model_loaded: bool = False


class SystemStatusOut(BaseModel):
    api: Literal["ready"] = "ready"
    embedding_state: Literal["idle", "loading", "ready", "error"]
    embedding_message: str
    embedding_model: str
    llm_provider: str = "groq"
    llm_fallback_enabled: bool = False
    llm_configured: bool = False
    llm_active_model: str | None = None
    llm_providers: list[LlmProviderStatusOut] = []
    groq_key_configured: bool
    groq_model_configured: bool
    groq_active_model: str | None = None
    groq_models_configured: int = 0
    active_nhh_documents: int
    nhh_chunks: int
    accessible_documents: int
    database_state: Literal["ready", "error"] = "ready"
    failed_nhh_documents: int = 0
    pending_nhh_documents: int = 0


class AiReadinessOut(BaseModel):
    legal_ready: bool
    general_ready: bool
    status: Literal["ready", "preparing", "unavailable"]
    message: str


class AnalysisRequest(BaseModel):
    operation: Literal["summary", "key_points", "qa", "contradictions"]
    question: str | None = Field(default=None, max_length=3000)


# Explicit UI selection, never inferred from the question text.
#   general -> one plain LLM answer, no retrieval, no grounding
#   legal   -> corpus retrieval + citations + grounding validation
#   auto    -> opt-in legal-intent routing (not used by the current UI)
ChatMode = Literal["general", "legal", "auto"]


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    mode: ChatMode | None = None
    # Legacy field for older clients; `mode` wins whenever it is supplied.
    legal: bool = True

    @property
    def resolved_mode(self) -> ChatMode:
        if self.mode is not None:
            return self.mode
        return "legal" if self.legal else "general"


class DraftRequest(BaseModel):
    kind: Literal["response_letter", "report", "info_note", "brief", "analytical_conclusion"]
    instruction: str = Field(min_length=2, max_length=4000)
    document_id: str | None = None
    recipient: str = Field(default="", max_length=500)
    document_date: date | None = None
    outgoing_number: str = Field(default="", max_length=120)


class NhhOut(OrmModel):
    id: str
    title: str
    category: str
    source_url: str | None
    official_number: str | None = None
    adoption_date: date | None = None
    original_filename: str
    indexed: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    extraction_status: str = "completed"
    indexing_status: str = "pending"
    processing_error: str | None = None
    uploaded_at: datetime
    indexed_at: datetime | None = None
    chunk_count: int = 0


class NhhUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=500)
    category: str | None = Field(default=None, min_length=2, max_length=120)
    source_url: str | None = Field(default=None, max_length=1000)
    official_number: str | None = Field(default=None, max_length=120)
    adoption_date: date | None = None
    is_active: bool | None = None

    @field_validator("source_url")
    @classmethod
    def safe_source_url(cls, value: str | None):
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("Havola http:// yoki https:// bilan boshlanishi kerak")
        return value


class TaskCreate(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    description: str = Field(default="", max_length=5000)
    assigned_to: str
    deadline: datetime
    priority: TaskPriority = TaskPriority.odatiy
    related_document_id: str | None = None

    @field_validator("related_document_id", mode="before")
    @classmethod
    def empty_document_is_none(cls, value):
        # The form sends "" when no document is chosen; "" is not a document id.
        return value or None

    @field_validator("deadline")
    @classmethod
    def timezone_required(cls, value: datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Muddat vaqt mintaqasi bilan yuborilishi kerak")
        return value


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    assigned_to: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    deadline: datetime | None = None
    related_document_id: str | None = None
    comment: str | None = Field(default=None, max_length=3000)

    @field_validator("related_document_id", mode="before")
    @classmethod
    def empty_document_is_none(cls, value):
        return value or None


class TaskOut(OrmModel):
    id: str
    title: str
    description: str
    assigned_to: str
    created_by: str
    status: TaskStatus
    priority: TaskPriority
    deadline: datetime
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    related_document_id: str | None = None
    assignee_name: str | None = None
    creator_name: str | None = None
    is_overdue: bool


class TaskEventOut(OrmModel):
    id: str
    task_id: str
    actor_id: str | None
    event_type: str
    message: str
    changes: dict
    created_at: datetime


class OrganizationProfileOut(OrmModel):
    id: str
    organization_name: str
    organization_name_secondary: str
    short_name: str
    parent_organization: str
    address: str
    phone: str
    email: str
    website: str
    tax_id: str
    outgoing_prefix: str
    department: str
    letterhead_text: str
    footer_text: str
    signatory_name: str
    signatory_title: str
    qr_verification_url: str
    barcode_text: str
    logo_stored_name: str | None
    updated_at: datetime


class OrganizationProfileUpdate(BaseModel):
    organization_name: str = Field(default="", max_length=300)
    organization_name_secondary: str = Field(default="", max_length=300)
    short_name: str = Field(default="", max_length=160)
    parent_organization: str = Field(default="", max_length=300)
    address: str = Field(default="", max_length=500)
    phone: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=200)
    website: str = Field(default="", max_length=300)
    tax_id: str = Field(default="", max_length=40)
    outgoing_prefix: str = Field(default="", max_length=60)
    department: str = Field(default="", max_length=200)
    letterhead_text: str = Field(default="", max_length=1000)
    footer_text: str = Field(default="", max_length=500)
    signatory_name: str = Field(default="", max_length=200)
    signatory_title: str = Field(default="", max_length=200)
    qr_verification_url: str = Field(default="", max_length=1000)
    barcode_text: str = Field(default="", max_length=200)


class HistoryOut(OrmModel):
    id: str
    user_id: str
    operation: str
    request_text: str
    response_text: str
    status: Literal["success", "fallback", "failure"] = "success"
    sources: list
    structured_data: dict | None = None
    document_id: str | None
    created_at: datetime


class AuditOut(OrmModel):
    id: str
    user_id: str | None
    action: str
    method: str
    path: str
    resource: str | None
    status_code: int
    created_at: datetime
