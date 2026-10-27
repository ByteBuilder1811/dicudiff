from app.core.db import session_scope
from app.models.tables import Chunk, Document, Unit
from app.services import retrieval
from app.services.embeddings import HashingEmbedder
from app.services.types import DocUnit


def seed(db):
    d = Document(original_name="x.pdf", stored_name="x", sha256="a" * 64, size_bytes=1, status="ready", page_count=2)
    db.add(d); db.flush()
    rows = [("heading", "1. Identity", 1, ""), ("paragraph", "The institution shall verify identity using an officially valid document.", 1, "1. Identity"),
            ("heading", "2. Fees", 2, ""), ("paragraph", "The annual maintenance fee is 5% of the outstanding balance.", 2, "2. Fees"),
            ("paragraph", "Late payment attracts a penalty of two percent per month.", 2, "2. Fees")]
    for i, (k, t, p, path) in enumerate(rows):
        db.add(Unit(document_id=d.id, idx=i, kind=k, page_start=p, page_end=p, section_path=path or t, text=t))
    db.flush()
    return d


def test_chunking_respects_headings_and_size():
    us = [DocUnit(0, "H", "heading", 1, 1, None, "H"), DocUnit(1, "x" * 500, "paragraph", 1, 1, None, "H"), DocUnit(2, "y" * 500, "paragraph", 1, 1, None, "H"),
          DocUnit(3, "long sentence. " * 200, "paragraph", 2, 2, None, "Other")]
    for u in us:
        u.page_start, u.page_end = u.page, u.page_end
    chunks = retrieval.chunk_units(us)
    assert all(len(c.text) <= 1000 for c in chunks) and len({c.heading for c in chunks}) == 2


def test_index_is_idempotent_and_embeds_once():
    emb = HashingEmbedder()
    with session_scope() as db:
        d = seed(db)
        a = retrieval.index_document(db, d.id, emb)
        b = retrieval.index_document(db, d.id, emb)
        assert a["created"] > 0 and b["created"] == 0 and b["embedded"] == 0 and b["removed"] == 0
        assert db.query(Chunk).filter_by(document_id=d.id).count() == a["chunks"]


def test_bm25_dense_hybrid_find_relevant_chunk():
    emb = HashingEmbedder()
    with session_scope() as db:
        d = seed(db)
        retrieval.index_document(db, d.id, emb)
        for mode in ("bm25", "dense", "hybrid"):
            hits, info = retrieval.search(db, [d.id], "maintenance fee percentage", 2, mode, emb)
            assert "maintenance fee" in hits[0].chunk.text, mode
            assert info["mode_used"] == mode
        hits, info = retrieval.search(db, [d.id], "maintenance fee", 2, "hybrid", None)
        assert info["mode_used"] == "bm25" and info["notes"]  # honest degradation


def test_citation_metadata_on_chunks():
    with session_scope() as db:
        d = seed(db)
        retrieval.index_document(db, d.id, None)
        hits, _ = retrieval.search(db, [d.id], "penalty late payment", 1, "bm25")
        assert hits[0].chunk.page_start == 2 and "Fees" in hits[0].chunk.heading
