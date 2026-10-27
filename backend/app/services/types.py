"""Plain dataclasses shared by the pure (DB-free) engine so it can be unit-tested in isolation."""
from dataclasses import dataclass, field

CATEGORIES = ("ADDED", "REMOVED", "MODIFIED", "UNCHANGED", "POSSIBLY_RELOCATED", "NEEDS_REVIEW")


@dataclass
class DocUnit:
    idx: int
    text: str  # original extracted text, numbering included
    kind: str = "paragraph"  # heading|paragraph|list|table
    page: int = 1
    page_end: int | None = None
    section_id: str | None = None
    section_path: str = ""
    # filled by prepare():
    body: str = ""  # text without leading clause number
    norm: str = ""  # normalized body (equality key)
    key: str = ""  # aggressive match key (case/punctuation-insensitive; matching only)


@dataclass
class ChangeRecord:
    category: str
    match_method: str
    explanation: str
    old: DocUnit | None = None
    new: DocUnit | None = None
    confidence: float | None = None
    flags: list[str] = field(default_factory=list)
    inline_diff: dict | None = None
    review_status: str = "none"


@dataclass
class ExtractionResult:
    pages: list[dict]  # {page_no, text, ocr}
    units: list[DocUnit]
    metadata: dict
    warnings: list[str]
    status: str  # ready | needs_ocr | failed
    error: str | None = None
