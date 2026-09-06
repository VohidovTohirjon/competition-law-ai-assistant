import enum
import uuid
from datetime import date, datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import get_settings
from .database import Base


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    administrator = "administrator"
    rahbar = "rahbar"
    xodim = "xodim"


class TaskStatus(str, enum.Enum):
    yangi = "yangi"
    jarayonda = "jarayonda"
    bajarildi = "bajarildi"
    bekor_qilindi = "bekor_qilindi"


class TaskPriority(str, enum.Enum):
    past = "past"
    odatiy = "odatiy"
    yuqori = "yuqori"
    shoshilinch = "shoshilinch"


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    # Stable identity for explicitly seeded operational samples. User uploads stay NULL;
    # filenames intentionally remain non-unique.
    seed_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True, index=True)
    media_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    parsed_text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), default="oddiy")
    is_confidential: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    owner: Mapped[User] = relationship()
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class NhhDocument(Base):
    __tablename__ = "nhh_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(500), index=True)
    category: Mapped[str] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    official_number: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    adoption_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    original_text: Mapped[str] = mapped_column(Text)
    indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    extraction_status: Mapped[str] = mapped_column(String(30), default="completed", index=True)
    indexing_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="nhh", cascade="all, delete-orphan")

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)


vector_type = JSON().with_variant(Vector(get_settings().embedding_dimensions), "postgresql")


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    corpus_type: Mapped[str] = mapped_column(String(20), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
    nhh_id: Mapped[str | None] = mapped_column(ForeignKey("nhh_documents.id", ondelete="CASCADE"), nullable=True, index=True)
    chunk_order: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    article_clause: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list[float]] = mapped_column(vector_type)
    document: Mapped[Document | None] = relationship(back_populates="chunks")
    nhh: Mapped[NhhDocument | None] = relationship(back_populates="chunks")


class AiHistory(Base):
    __tablename__ = "ai_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    operation: Mapped[str] = mapped_column(String(80), index=True)
    request_text: Mapped[str] = mapped_column(Text)
    response_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="success", index=True)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    structured_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    assigned_to: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus, native_enum=False, length=20), default=TaskStatus.yangi)
    priority: Mapped[TaskPriority] = mapped_column(Enum(TaskPriority, native_enum=False, length=20), default=TaskPriority.odatiy, index=True)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    related_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True)
    seed_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assignee: Mapped[User] = relationship(foreign_keys=[assigned_to])
    creator: Mapped[User] = relationship(foreign_keys=[created_by])
    related_document: Mapped[Document | None] = relationship()
    events: Mapped[list["TaskEvent"]] = relationship(back_populates="task", cascade="all, delete-orphan")

    @property
    def is_overdue(self) -> bool:
        deadline = self.deadline
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return self.status not in {TaskStatus.bajarildi, TaskStatus.bekor_qilindi} and deadline < now()

    @property
    def assignee_name(self) -> str | None:
        return self.assignee.full_name if self.assignee else None

    @property
    def creator_name(self) -> str | None:
        return self.creator.full_name if self.creator else None


class TaskEvent(Base):
    __tablename__ = "task_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    changes: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    task: Mapped[Task] = relationship(back_populates="events")


class OrganizationProfile(Base):
    __tablename__ = "organization_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    organization_name: Mapped[str] = mapped_column(String(300), default="")
    organization_name_secondary: Mapped[str] = mapped_column(String(300), default="")
    short_name: Mapped[str] = mapped_column(String(160), default="")
    parent_organization: Mapped[str] = mapped_column(String(300), default="")
    address: Mapped[str] = mapped_column(String(500), default="")
    phone: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    website: Mapped[str] = mapped_column(String(300), default="")
    tax_id: Mapped[str] = mapped_column(String(40), default="")
    outgoing_prefix: Mapped[str] = mapped_column(String(60), default="")
    department: Mapped[str] = mapped_column(String(200), default="")
    letterhead_text: Mapped[str] = mapped_column(String(1000), default="")
    footer_text: Mapped[str] = mapped_column(String(500), default="")
    signatory_name: Mapped[str] = mapped_column(String(200), default="")
    signatory_title: Mapped[str] = mapped_column(String(200), default="")
    qr_verification_url: Mapped[str] = mapped_column(String(1000), default="")
    barcode_text: Mapped[str] = mapped_column(String(200), default="")
    logo_stored_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(500))
    resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_code: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
