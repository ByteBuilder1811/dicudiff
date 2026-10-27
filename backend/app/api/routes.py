"""HTTP API. Every endpoint is documented in docs/API.md and in the generated OpenAPI schema (/docs)."""
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session, unlink_after_commit
from app.core.limits import api_error, rate_limit, require_token
from app.models.tables import Change, Comparison, Document, Page, Report, Unit
from app.schemas import api as S
from app.services import common_removals as cr
from app.services import local_llm, pipeline, rag, reporting
from app.services.embeddings import embedder_status
from app.services.normalization import normalize

log = logging.getLogger("docudiff.api")
router = APIRouter(prefix="/api", dependencies=[Depends(rate_limit), Depends(require_token)])
CATS = ["ADDED", "REMOVED", "MODIFIED", "UNCHANGED", "POSSIBLY_RELOCATED", "NEEDS_REVIEW"]


def _id(s: str) -> str:
    if not (s.isascii() and s.isalnum()) or len(s) > 40:  # ids are hex; reject anything else early
        raise api_error(422, "bad_id", "Malformed identifier.")
    return s


def _doc(db: Session, doc_id: str) -> Document:
    d = db.get(Document, _id(doc_id))
    if not d:
        raise api_error(404, "document_not_found", "Document not found.")
    return d


def _comp(db: Session, cid: str) -> Comparison:
    c = db.get(Comparison, _id(cid))
    if not c:
        raise api_error(404, "comparison_not_found", "Comparison not found.")
    return c


def _comp_out(db: Session, c: Comparison) -> S.ComparisonOut:
    out = S.ComparisonOut.model_validate(c)
    a, b = db.get(Document, c.doc_a_id), db.get(Document, c.doc_b_id)
    out.doc_a_name, out.doc_b_name = (a.original_name if a else None), (b.original_name if b else None)
    return out


# ------------------------------------------------------------------ system
@router.get("/health", tags=["system"])
def health():
    s = get_settings()
    return {"status": "ok", "version": "1.0.0", "llm": local_llm.status(), "embeddings": embedder_status(),
            "ocr_enabled": s.ocr_enabled, "limits": {"max_upload_mb": s.max_upload_mb, "max_pages": s.max_pages},
            "retention_days": s.retention_days}


@router.get("/stats", tags=["system"])
def stats(db: Session = Depends(get_session)):
    counts = dict(db.execute(select(Change.category, func.count()).group_by(Change.category)).all())
    return {"documents": db.scalar(select(func.count()).select_from(Document)),
            "comparisons": db.scalar(select(func.count()).select_from(Comparison)),
            "completed": db.scalar(select(func.count()).select_from(Comparison).where(Comparison.status == "completed")),
            "running": db.scalar(select(func.count()).select_from(Comparison).where(Comparison.status.in_(["queued", "running"]))),
            "failed": db.scalar(select(func.count()).select_from(Comparison).where(Comparison.status == "failed")),
            "changes_by_category": {c: counts.get(c, 0) for c in CATS}}


# --------------------------------------------------------------- documents
@router.post("/documents/upload", response_model=S.UploadOut, tags=["documents"], status_code=201)
def upload(file: UploadFile = File(...), label: str | None = Form(None, max_length=80), db: Session = Depends(get_session)):
    cfg = get_settings()
    name = Path((file.filename or "document.pdf").replace("\\", "/")).name  # display only; never used as a path
    name = "".join(ch for ch in name if ch.isprintable()).strip() or "document.pdf"
    if not name.lower().endswith(".pdf"):
        raise api_error(415, "unsupported_type", "Only .pdf files are accepted.")
    tmp = cfg.upload_dir / f"tmp-{uuid.uuid4().hex}"
    size, limit = 0, cfg.max_upload_mb * 1024 * 1024
    try:
        with open(tmp, "wb") as out:
            head = file.file.read(1024)
            if not head:
                raise api_error(422, "empty_file", "The uploaded file is empty.")
            if not head.lstrip(b"\x00 \r\n\t").startswith(b"%PDF-"):
                raise api_error(415, "not_a_pdf", "File content is not a PDF (missing %PDF header).")
            chunk = head
            while chunk:
                size += len(chunk)
                if size > limit:
                    raise api_error(413, "too_large", f"File exceeds the {cfg.max_upload_mb} MB limit.")
                out.write(chunk)
                chunk = file.file.read(1 << 20)
        if size == 0:
            raise api_error(422, "empty_file", "The uploaded file is empty.")
        doc, dedup = pipeline.ingest_pdf(db, tmp, name, label)
    finally:
        tmp.unlink(missing_ok=True)
    db.flush()
    return S.UploadOut(document=S.DocumentOut.model_validate(doc), deduplicated=dedup)


@router.get("/documents", response_model=S.DocumentList, tags=["documents"])
def list_documents(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_session)):
    total = db.scalar(select(func.count()).select_from(Document))
    rows = db.scalars(select(Document).order_by(Document.created_at.desc()).limit(limit).offset(offset)).all()
    return S.DocumentList(total=total, limit=limit, offset=offset, items=[S.DocumentOut.model_validate(r) for r in rows])


@router.get("/documents/{document_id}", response_model=S.DocumentDetail, tags=["documents"])
def get_document(document_id: str, include_text: bool = False, db: Session = Depends(get_session)):
    d = _doc(db, document_id)
    pages = db.scalars(select(Page).where(Page.document_id == d.id).order_by(Page.page_no)).all()
    out = S.DocumentDetail(**S.DocumentOut.model_validate(d).model_dump(), unit_count=db.scalar(select(func.count()).select_from(Unit).where(Unit.document_id == d.id)),
                           pages=[S.PageInfo(page_no=p.page_no, char_count=p.char_count, ocr=p.ocr, text=p.text if include_text else None) for p in pages])
    return out


@router.delete("/documents/{document_id}", tags=["documents"])
def delete_document(document_id: str, db: Session = Depends(get_session)):
    d = _doc(db, document_id)
    return {"deleted": d.id, **pipeline.delete_document(db, d)}


# ------------------------------------------------------------- comparisons
@router.post("/comparisons", response_model=S.ComparisonOut, status_code=202, tags=["comparisons"])
def create_comparison(body: S.ComparisonCreate, bg: BackgroundTasks, db: Session = Depends(get_session)):
    if body.doc_a_id == body.doc_b_id:
        raise api_error(422, "same_document", "Choose two different documents to compare.")
    a, b = _doc(db, body.doc_a_id), _doc(db, body.doc_b_id)
    for lbl, d in (("Version A", a), ("Version B", b)):
        if d.status != "ready":
            raise api_error(422, "document_not_ready", f"{lbl} ({d.original_name}) has no extractable text: {d.error or 'scanned/image-only PDF; enable OCR'}.")
    c = Comparison(doc_a_id=a.id, doc_b_id=b.id, label_a=body.label_a, label_b=body.label_b, options=body.options.model_dump())
    db.add(c)
    db.flush()
    db.commit()
    bg.add_task(pipeline.run_comparison, c.id)
    return _comp_out(db, c)


@router.get("/comparisons", response_model=S.ComparisonList, tags=["comparisons"])
def list_comparisons(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_session)):
    total = db.scalar(select(func.count()).select_from(Comparison))
    rows = db.scalars(select(Comparison).order_by(Comparison.created_at.desc()).limit(limit).offset(offset)).all()
    return S.ComparisonList(total=total, limit=limit, offset=offset, items=[_comp_out(db, r) for r in rows])


@router.get("/comparisons/{comparison_id}", response_model=S.ComparisonOut, tags=["comparisons"])
def get_comparison(comparison_id: str, db: Session = Depends(get_session)):
    return _comp_out(db, _comp(db, comparison_id))


@router.post("/comparisons/{comparison_id}/retry", response_model=S.ComparisonOut, status_code=202, tags=["comparisons"])
def retry_comparison(comparison_id: str, bg: BackgroundTasks, db: Session = Depends(get_session)):
    c = _comp(db, comparison_id)
    if c.status not in ("failed",):
        raise api_error(409, "not_failed", "Only failed comparisons can be retried.")
    pipeline.reset_for_retry(db, c)
    db.commit()
    bg.add_task(pipeline.run_comparison, c.id)
    return _comp_out(db, c)


@router.delete("/comparisons/{comparison_id}", tags=["comparisons"])
def delete_comparison(comparison_id: str, db: Session = Depends(get_session)):
    c = _comp(db, comparison_id)
    cfg = get_settings()
    for r in db.scalars(select(Report).where(Report.comparison_id == c.id)):
        unlink_after_commit(db, cfg.report_dir / r.stored_name)
    db.delete(c)
    return {"deleted": comparison_id}


@router.get("/comparisons/{comparison_id}/changes", response_model=S.ChangeList, tags=["comparisons"])
def list_changes(comparison_id: str, category: str | None = Query(None, description="Comma-separated categories"),
                 q: str | None = Query(None, max_length=200, description="Substring search in old/new text"),
                 risk_only: bool = False, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                 db: Session = Depends(get_session)):
    c = _comp(db, comparison_id)
    stmt = select(Change).where(Change.comparison_id == c.id)
    if category:
        cats = [x.strip().upper() for x in category.split(",") if x.strip()]
        if any(x not in CATS for x in cats):
            raise api_error(422, "bad_category", f"Category must be one of {CATS}.")
        stmt = stmt.where(Change.category.in_(cats))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Change.old_text).like(like), func.lower(Change.new_text).like(like)))
    rows = db.scalars(stmt.order_by(Change.seq)).all()
    if risk_only:
        rows = [r for r in rows if set(r.flags or []) & reporting.RISK]
    counts = dict(db.execute(select(Change.category, func.count()).where(Change.comparison_id == c.id).group_by(Change.category)).all())
    return S.ChangeList(total=len(rows), limit=limit, offset=offset, counts={k: counts.get(k, 0) for k in CATS},
                        items=[S.ChangeOut.model_validate(r) for r in rows[offset: offset + limit]])


@router.post("/comparisons/{comparison_id}/changes/{change_id}/review", response_model=S.ChangeOut, tags=["review"])
def review_change(comparison_id: str, change_id: str, body: S.ReviewIn, db: Session = Depends(get_session)):
    c = _comp(db, comparison_id)
    ch = db.get(Change, _id(change_id))
    if not ch or ch.comparison_id != c.id:
        raise api_error(404, "change_not_found", "Change not found.")
    if ch.review_status != "pending":
        raise api_error(409, "not_reviewable", "Only changes awaiting review can be reviewed.")
    if body.decision == "confirm":
        same = normalize(ch.old_text or "") == normalize(ch.new_text or "")
        ch.category = "POSSIBLY_RELOCATED" if "relocated" in (ch.flags or []) else ("UNCHANGED" if same else "MODIFIED")
        ch.review_status = "confirmed"
        ch.explanation += " [Human review: confirmed as the same provision.]"
    else:  # split into REMOVED + ADDED, keeping order
        for later in db.scalars(select(Change).where(Change.comparison_id == c.id, Change.seq > ch.seq)):
            later.seq += 1
        db.add(Change(comparison_id=c.id, seq=ch.seq + 1, category="ADDED", match_method="human_review", explanation="Human review: rejected the proposed match; treated as new content.",
                      new_text=ch.new_text, new_page=ch.new_page, new_section=ch.new_section, new_unit_idx=ch.new_unit_idx, review_status="rejected"))
        ch.category, ch.match_method, ch.review_status = "REMOVED", "human_review", "rejected"
        ch.explanation = "Human review: rejected the proposed match; treated as removed content."
        ch.new_text = ch.new_page = ch.new_section = ch.new_unit_idx = ch.inline_diff = None
        ch.confidence, ch.flags = None, []
    db.flush()
    rows = db.scalars(select(Change).where(Change.comparison_id == c.id)).all()
    from app.services.diff_engine import summarize
    from app.services.types import ChangeRecord
    c.stats = {**c.stats, **summarize([ChangeRecord(r.category, r.match_method, "", None, None, None, r.flags or [], None, r.review_status) for r in rows])}
    return S.ChangeOut.model_validate(ch)


@router.post("/comparisons/{comparison_id}/changes/{change_id}/explain", response_model=S.ChangeOut, tags=["ai"])
def explain(comparison_id: str, change_id: str, db: Session = Depends(get_session)):
    c = _comp(db, comparison_id)
    ch = db.get(Change, _id(change_id))
    if not ch or ch.comparison_id != c.id:
        raise api_error(404, "change_not_found", "Change not found.")
    try:
        ch.ai_explanation = local_llm.explain_change(ch.category, ch.old_text, ch.new_text, ch.flags or [])
    except local_llm.LLMUnavailable as exc:
        raise api_error(503, "llm_unavailable", str(exc))
    db.flush()
    return S.ChangeOut.model_validate(ch)


@router.post("/comparisons/{comparison_id}/questions", tags=["ai"])
def ask(comparison_id: str, body: S.QuestionIn, db: Session = Depends(get_session)):
    c = _comp(db, comparison_id)
    if c.status != "completed":
        raise api_error(409, "not_ready", "The comparison has not finished processing.")
    return rag.answer(db, c, body.question, body.k, body.use_llm, body.retrieval_mode, body.rerank)


# ----------------------------------------------------------------- reports
@router.post("/comparisons/{comparison_id}/export", response_model=S.ReportOut, tags=["reports"])
def export(comparison_id: str, body: S.ExportIn, db: Session = Depends(get_session)):
    c = _comp(db, comparison_id)
    if c.status != "completed":
        raise api_error(409, "not_ready", "The comparison has not finished processing.")
    cfg = get_settings()
    data = reporting.collect(db, c, body.include_unchanged)
    stored = f"{uuid.uuid4().hex}.{body.format}"
    path = cfg.report_dir / stored
    old_reports = db.scalars(select(Report).where(Report.comparison_id == c.id, Report.format == body.format)).all()
    try:
        if body.format == "md":
            path.write_text(reporting.to_markdown(data), encoding="utf-8")
        elif body.format == "html":
            path.write_text(reporting.to_html(data), encoding="utf-8")
        else:
            reporting.to_pdf(data, path)
    except BaseException:
        path.unlink(missing_ok=True)  # no half-written report files
        raise
    for old in old_reports:  # keep only the latest export per comparison+format
        (cfg.report_dir / old.stored_name).unlink(missing_ok=True)
        db.delete(old)
    r = Report(comparison_id=c.id, format=body.format, stored_name=stored)
    db.add(r)
    db.flush()
    out = S.ReportOut.model_validate(r)
    out.download_url = f"/api/reports/{r.id}/download"
    return out


@router.get("/reports/{report_id}/download", tags=["reports"])
def download_report(report_id: str, db: Session = Depends(get_session)):
    r = db.get(Report, _id(report_id))
    path = get_settings().report_dir / (r.stored_name if r else "missing")
    if not r or not path.is_file():
        raise api_error(404, "report_not_found", "Report not found.")
    media = {"md": "text/markdown", "html": "text/html", "pdf": "application/pdf"}[r.format]
    return FileResponse(path, media_type=media, filename=f"docudiff-report-{r.comparison_id[:8]}.{r.format}",
                        headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"})


@router.post("/common-removals", tags=["analysis"])
def common_removals(body: S.CommonRemovalIn, db: Session = Depends(get_session)):
    a, b = _comp(db, body.comparison_a_id), _comp(db, body.comparison_b_id)
    if a.id == b.id:
        raise api_error(422, "same_comparison", "Choose two different comparison pairs.")
    for c in (a, b):
        if c.status != "completed":
            raise api_error(409, "not_ready", "Both comparisons must be completed.")
    return cr.common_removals(db, a, b)
