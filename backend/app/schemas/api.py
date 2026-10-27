"""Request/response schemas (also the source of the OpenAPI docs at /docs)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["ADDED", "REMOVED", "MODIFIED", "UNCHANGED", "POSSIBLY_RELOCATED", "NEEDS_REVIEW"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DocumentOut(ORM):
    id: str
    original_name: str
    label: str | None
    status: Literal["ready", "needs_ocr", "failed", "processing"]
    error: str | None
    page_count: int
    size_bytes: int
    sha256: str
    meta: dict
    warnings: list[str]
    created_at: datetime


class UploadOut(BaseModel):
    document: DocumentOut
    deduplicated: bool = Field(description="True when identical bytes were already stored; no new record was created.")


class PageInfo(ORM):
    page_no: int
    char_count: int
    ocr: bool
    text: str | None = None


class DocumentDetail(DocumentOut):
    pages: list[PageInfo]
    unit_count: int


class Paged(BaseModel):
    total: int
    limit: int
    offset: int


class DocumentList(Paged):
    items: list[DocumentOut]


class ComparisonOptions(BaseModel):
    semantic: bool = Field(True, description="Use local embeddings to propose reworded matches (NEEDS_REVIEW only).")
    ignore_punctuation_case: bool = False
    renumbering_is_change: bool = False


class ComparisonCreate(BaseModel):
    doc_a_id: str = Field(description="Older document (Version A)")
    doc_b_id: str = Field(description="Newer document (Version B)")
    label_a: str = Field("Version A", max_length=80)
    label_b: str = Field("Version B", max_length=80)
    options: ComparisonOptions = ComparisonOptions()


class ComparisonOut(ORM):
    id: str
    doc_a_id: str
    doc_b_id: str
    label_a: str
    label_b: str
    status: Literal["queued", "running", "completed", "failed"]
    stage: str
    progress: float
    error: str | None
    options: dict
    stats: dict
    limitations: list[str]
    created_at: datetime
    finished_at: datetime | None
    doc_a_name: str | None = None
    doc_b_name: str | None = None


class ComparisonList(Paged):
    items: list[ComparisonOut]


class ChangeOut(ORM):
    id: str
    seq: int
    category: Category
    old_text: str | None
    new_text: str | None
    old_page: int | None
    new_page: int | None
    old_section: str | None
    new_section: str | None
    match_method: str
    confidence: float | None
    explanation: str
    flags: list[str]
    inline_diff: dict | None
    review_status: str
    ai_explanation: dict | None


class ChangeList(Paged):
    items: list[ChangeOut]
    counts: dict[str, int]


class ReviewIn(BaseModel):
    decision: Literal["confirm", "reject"] = Field(description="confirm: same provision. reject: unrelated => REMOVED + ADDED.")


class QuestionIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    k: int = Field(6, ge=1, le=12)
    use_llm: bool = True
    retrieval_mode: Literal["hybrid", "bm25", "dense"] = "hybrid"
    rerank: bool = False


class ExportIn(BaseModel):
    format: Literal["md", "html", "pdf"] = "html"
    include_unchanged: bool = False


class ReportOut(ORM):
    id: str
    comparison_id: str
    format: str
    created_at: datetime
    download_url: str | None = None


class CommonRemovalIn(BaseModel):
    comparison_a_id: str = Field(description="Pair A: A1 → A2")
    comparison_b_id: str = Field(description="Pair B: B1 → B2")
