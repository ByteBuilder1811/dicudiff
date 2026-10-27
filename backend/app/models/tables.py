"""ORM tables. Schema is created with metadata.create_all (see docs/ARCHITECTURE.md for migrations note)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="processing")  # ready|needs_ocr|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    pages = relationship("Page", cascade="all, delete-orphan", passive_deletes=True, order_by="Page.page_no")
    units = relationship("Unit", cascade="all, delete-orphan", passive_deletes=True, order_by="Unit.idx")


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer)
    ocr: Mapped[bool] = mapped_column(default=False)


class Unit(Base):
    """A paragraph/heading/list/table block extracted from a document, in reading order."""
    __tablename__ = "units"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # heading|paragraph|list|table
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    section_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    section_path: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text)  # original extracted text (with numbering)


class Comparison(Base):
    __tablename__ = "comparisons"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    doc_a_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    doc_b_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    label_a: Mapped[str] = mapped_column(String(80), default="Version A")
    label_b: Mapped[str] = mapped_column(String(80), default="Version B")
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|completed|failed
    stage: Mapped[str] = mapped_column(String(40), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    limitations: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    changes = relationship("Change", cascade="all, delete-orphan", passive_deletes=True, order_by="Change.seq")


class Change(Base):
    __tablename__ = "changes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    comparison_id: Mapped[str] = mapped_column(ForeignKey("comparisons.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(20), index=True)
    old_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    old_section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    old_unit_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_unit_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_method: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    explanation: Mapped[str] = mapped_column(Text, default="")
    flags: Mapped[list] = mapped_column(JSON, default=list)
    inline_diff: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    review_status: Mapped[str] = mapped_column(String(16), default="none")  # none|pending|confirmed|rejected
    ai_explanation: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("document_id", "idx"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # deterministic hash => idempotent indexing
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(Text, default="")
    section_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    comparison_id: Mapped[str] = mapped_column(ForeignKey("comparisons.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(10))
    stored_name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=_now)
