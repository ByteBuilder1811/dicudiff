"""Orchestration: document ingestion and the comparison job. Keeps routers thin and the engine DB-free."""
import hashlib
import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import session_scope, unlink_after_commit
from app.models.tables import Change, Chunk, Comparison, Document, Page, Unit
from app.services import retrieval
from app.services.diff_engine import compare_units, summarize
from app.services.embeddings import get_embedder
from app.services.normalization import NormalizationConfig
from app.services.pdf_extraction import extract_pdf, extract_pdf_isolated
from app.services.section_alignment import AlignConfig, AlignTimeout
from app.services.types import DocUnit

log = logging.getLogger("docudiff.pipeline")


class Timeout(Exception):
    pass


class Gone(Exception):
    """The comparison row was deleted while its job was running."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_pdf(db: Session, tmp_path: Path, original_name: str, label: str | None) -> tuple[Document, bool]:
    """Returns (document, deduplicated). Identical bytes are stored once, so re-uploading never duplicates records."""
    cfg = get_settings()
    digest = sha256_file(tmp_path)
    existing = db.scalar(select(Document).where(Document.sha256 == digest))
    if existing:
        if label:
            existing.label = label
        tmp_path.unlink(missing_ok=True)
        return existing, True
    stored = f"{uuid.uuid4().hex}.pdf"  # generated filename: user-supplied names never touch the filesystem
    final = cfg.upload_dir / stored
    shutil.move(str(tmp_path), final)
    try:
        res = extract_pdf_isolated(final) if cfg.isolate_extraction else extract_pdf(final)
        doc = Document(original_name=original_name[:255], stored_name=stored, sha256=digest,
                       size_bytes=final.stat().st_size, label=label, status=res.status, error=res.error,
                       page_count=len(res.pages), meta=res.metadata, warnings=res.warnings)
        db.add(doc)
        db.flush()
        for p in res.pages:
            db.add(Page(document_id=doc.id, page_no=p["page_no"], text=p["text"], char_count=len(p["text"]), ocr=p["ocr"]))
        for u in res.units:
            db.add(Unit(document_id=doc.id, idx=u.idx, kind=u.kind, page_start=u.page, page_end=u.page_end or u.page,
                        section_id=u.section_id, section_path=u.section_path, text=u.text))
        db.flush()
        if res.status == "ready":
            retrieval.index_document(db, doc.id, None)  # chunks now, embeddings during the comparison job / lazily
    except IntegrityError:
        # Concurrent upload of identical bytes won the sha256 unique constraint: reuse its record.
        db.rollback()
        final.unlink(missing_ok=True)
        existing = db.scalar(select(Document).where(Document.sha256 == digest))
        if existing is None:
            raise
        if label:
            existing.label = label
        return existing, True
    except BaseException:
        final.unlink(missing_ok=True)  # never leave an orphaned stored file behind a failed ingest
        raise
    return doc, False


def delete_document(db: Session, doc: Document) -> dict:
    """Retention rule: deleting a document removes its file, pages, units, chunks (and embeddings) and every
    comparison (with changes and reports) that references it."""
    from app.models.tables import Report
    comps = db.scalars(select(Comparison).where((Comparison.doc_a_id == doc.id) | (Comparison.doc_b_id == doc.id))).all()
    cfg = get_settings()
    for c in comps:
        for r in db.scalars(select(Report).where(Report.comparison_id == c.id)):
            unlink_after_commit(db, cfg.report_dir / r.stored_name)
        db.delete(c)
    unlink_after_commit(db, cfg.upload_dir / doc.stored_name)
    db.delete(doc)
    return {"comparisons_deleted": len(comps)}


def load_units(db: Session, document_id: str) -> list[DocUnit]:
    rows = db.scalars(select(Unit).where(Unit.document_id == document_id).order_by(Unit.idx)).all()
    return [DocUnit(idx=r.idx, text=r.text, kind=r.kind, page=r.page_start, page_end=r.page_end, section_id=r.section_id, section_path=r.section_path) for r in rows]


def _set(comp_id: str, **kw):
    with session_scope() as db:
        c = db.get(Comparison, comp_id)
        if c is None:
            raise Gone(comp_id)
        for k, v in kw.items():
            setattr(c, k, v)


def run_comparison(comp_id: str) -> None:
    """Background job. Never raises: failures are stored on the comparison (status=failed) so they can be retried."""
    cfg = get_settings()
    t0 = time.monotonic()
    deadline = t0 + cfg.comparison_timeout_s

    def check(stage: str, progress: float):
        if time.monotonic() > deadline:
            raise Timeout(f"Comparison exceeded the {cfg.comparison_timeout_s}s limit during '{stage}'.")
        _set(comp_id, status="running", stage=stage, progress=progress)

    try:
        check("loading", 0.05)
        with session_scope() as db:
            comp = db.get(Comparison, comp_id)
            opts = dict(comp.options or {})
            a, b = db.get(Document, comp.doc_a_id), db.get(Document, comp.doc_b_id)
            limitations = [f"{lbl} ({d.original_name}): {w}" for lbl, d in ((comp.label_a, a), (comp.label_b, b)) for w in (d.warnings or [])]
            for lbl, d in ((comp.label_a, a), (comp.label_b, b)):
                if d.status != "ready":
                    raise ValueError(f"{lbl} has no extractable text ({d.status}); comparison is not meaningful.")
            old_units, new_units = load_units(db, a.id), load_units(db, b.id)
        ncfg = NormalizationConfig(ignore_punctuation_case=bool(opts.get("ignore_punctuation_case")),
                                   renumbering_is_change=bool(opts.get("renumbering_is_change")))
        embedder = get_embedder() if opts.get("semantic", True) else None
        if opts.get("semantic", True) and (embedder is None or not getattr(embedder, "semantic", False)):
            limitations.append("No local semantic embedding model was available, so reworded provisions without lexical overlap are reported as REMOVED + ADDED.")
        check("aligning", 0.25)
        embed_fn = embedder.embed if embedder is not None and getattr(embedder, "semantic", False) else None
        try:  # the deadline is enforced inside the O(n*m) alignment loops, not only between stages
            records, notes = compare_units(old_units, new_units, ncfg, AlignConfig(deadline=deadline), embed_fn)
        except AlignTimeout:
            raise Timeout(f"Comparison exceeded the {cfg.comparison_timeout_s}s limit during 'aligning'.") from None
        limitations += [n for n in notes if n not in limitations and "not run" not in n]
        check("saving", 0.7)
        stats = summarize(records)
        with session_scope() as db:
            for seq, r in enumerate(records):
                db.add(Change(
                    comparison_id=comp_id, seq=seq, category=r.category, match_method=r.match_method, confidence=r.confidence,
                    explanation=r.explanation, flags=r.flags, inline_diff=r.inline_diff, review_status=r.review_status,
                    old_text=r.old.text if r.old else None, new_text=r.new.text if r.new else None,
                    old_page=r.old.page if r.old else None, new_page=r.new.page if r.new else None,
                    old_section=(r.old.section_id or r.old.section_path or None) if r.old else None,
                    new_section=(r.new.section_id or r.new.section_path or None) if r.new else None,
                    old_unit_idx=r.old.idx if r.old else None, new_unit_idx=r.new.idx if r.new else None))
            if stats["pending_review"]:
                limitations.append(f"{stats['pending_review']} ambiguous match(es) await human review.")
        check("indexing", 0.85)
        with session_scope() as db:
            comp = db.get(Comparison, comp_id)
            for did in (comp.doc_a_id, comp.doc_b_id):
                retrieval.index_document(db, did, embedder)
        stats["duration_s"] = round(time.monotonic() - t0, 2)
        _set(comp_id, status="completed", stage="done", progress=1.0, stats=stats, limitations=limitations,
             finished_at=datetime.now(timezone.utc).replace(tzinfo=None), error=None)
    except Gone:
        log.info("comparison %s was deleted while running; job abandoned", comp_id)
    except Exception as exc:  # log the type only: tracebacks may embed document text
        try:
            if isinstance(exc, Timeout):
                _set(comp_id, status="failed", stage="timeout", error=str(exc))
            elif isinstance(exc, ValueError):
                _set(comp_id, status="failed", stage="invalid_input", error=str(exc))
            else:
                log.error("comparison %s failed: %s", comp_id, type(exc).__name__)
                _set(comp_id, status="failed", stage="error", error=f"Internal error ({type(exc).__name__}). You can retry the comparison.")
        except Gone:
            pass


def recover_interrupted() -> int:
    """Startup: a job that was queued/running when the process died will never finish. Mark it failed (retryable).
    Assumes a single server process (the documented deployment)."""
    n = 0
    with session_scope() as db:
        for c in db.scalars(select(Comparison).where(Comparison.status.in_(["queued", "running"]))):
            c.status, c.stage = "failed", "interrupted"
            c.error = "The server restarted while this comparison was running. Use Retry to run it again."
            n += 1
    return n


def reset_for_retry(db: Session, comp: Comparison) -> None:
    for ch in db.scalars(select(Change).where(Change.comparison_id == comp.id)):
        db.delete(ch)
    comp.status, comp.stage, comp.progress, comp.error, comp.stats, comp.limitations = "queued", "queued", 0.0, None, {}, []
