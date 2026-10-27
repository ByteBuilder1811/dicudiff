"""Chunking, indexing (idempotent), BM25 + dense + hybrid retrieval (RRF), optional cross-encoder reranking."""
import hashlib
from collections import Counter
import re
import threading
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.tables import Chunk, Unit

MAX_CHARS = 900
RRF_K = 60
STOP = set("a an the of to in on for and or is are was were be been by with as at from that this which what who whom how did does do "
           "any it its their there than then into not no many much when why can could would should will have has had you your i me we per".split())


class BM25:
    """Okapi BM25 with the always-positive Lucene IDF, so tiny corpora (a few chunks) still rank sensibly."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b, self.docs = k1, b, docs
        self.tf = [dict(Counter(d)) for d in docs]
        self.avg = (sum(len(d) for d in docs) / len(docs)) or 1.0
        n = len(docs)
        df: dict[str, int] = {}
        for d in self.tf:
            for t in d:
                df[t] = df.get(t, 0) + 1
        self.idf = {t: float(np.log(1 + (n - c + 0.5) / (c + 0.5))) for t, c in df.items()}

    def get_scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(len(self.docs))
        for i, (tf, d) in enumerate(zip(self.tf, self.docs)):
            norm = self.k1 * (1 - self.b + self.b * len(d) / self.avg)
            for t in set(query):
                f = tf.get(t)
                if f:
                    out[i] += self.idf[t] * f * (self.k1 + 1) / (f + norm)
        return out


# Grouped numbers: western (1,000,000) and Indian (10,00,000) separators, optional all-zero decimals (.00).
_GROUPED_NUM = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.0+)?(?![\d,])")
_SCALED_NUM = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)\s*(lakhs?|lacs?|crores?)\b", re.I)
_SCALE = {"lakh": 100_000, "lac": 100_000, "crore": 10_000_000}


def _scaled(m: re.Match) -> str:
    val = float(m.group(1)) * _SCALE[m.group(2).lower().rstrip("s")]
    return str(int(val)) if val == int(val) else m.group(0)


def normalize_numbers(text: str) -> str:
    """Make '10,00,000', '1,000,000', '1,000,000.00' and '10 lakh' all become '1000000' (for BM25 only; the stored/embedded
    text is never altered). Lists like '1,2,3' or '12,34' are left alone because they are not valid digit grouping."""
    text = _SCALED_NUM.sub(_scaled, text)
    return _GROUPED_NUM.sub(lambda m: m.group(1).replace(",", ""), text)


def tokenize(text: str) -> list[str]:
    text = normalize_numbers(text)
    return re.findall(r"\w+(?:[.,%]\w+)*%?", text.lower())


def content_terms(text: str) -> list[str]:
    return [t for t in tokenize(text) if t not in STOP and len(t) > 1]


@dataclass
class ChunkDraft:
    idx: int
    text: str
    heading: str
    section_id: str | None
    page_start: int
    page_end: int


def _hard_split(sentence: str) -> list[str]:
    """A 'sentence' longer than MAX_CHARS (no punctuation, e.g. a table dump) is split on word boundaries so that no
    chunk is unbounded (embedding models truncate silently, BM25 length normalisation degrades)."""
    if len(sentence) <= MAX_CHARS:
        return [sentence]
    out, cur = [], ""
    for w in sentence.split():
        if cur and len(cur) + len(w) + 1 > MAX_CHARS:
            out.append(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
        while len(cur) > MAX_CHARS:  # a single token longer than the limit
            out.append(cur[:MAX_CHARS]); cur = cur[MAX_CHARS:]
    if cur:
        out.append(cur)
    return out


def chunk_units(units: list) -> list[ChunkDraft]:
    """Group consecutive units under the same heading path up to MAX_CHARS; never cross a heading path boundary.
    A single over-long paragraph is split on sentence boundaries with one sentence of overlap (the only overlap used)."""
    drafts: list[ChunkDraft] = []
    cur: list = []

    def flush():
        nonlocal cur
        if cur:
            text = "\n".join(u.text for u in cur)
            drafts.append(ChunkDraft(len(drafts), text, cur[0].section_path, next((u.section_id for u in cur if u.section_id), None),
                                     cur[0].page_start, cur[-1].page_end))
            cur = []

    for u in units:
        if cur and (u.section_path != cur[0].section_path or sum(len(c.text) for c in cur) + len(u.text) > MAX_CHARS or u.kind == "heading"):
            flush()
        if len(u.text) > MAX_CHARS:
            flush()
            sents = [part for sent in re.split(r"(?<=[.;:!?])\s+", u.text) for part in _hard_split(sent)]
            buf: list[str] = []
            fresh = False  # True when buf holds something beyond the one-sentence overlap
            for s in sents:
                if buf and sum(len(x) for x in buf) + len(s) + len(buf) > MAX_CHARS:
                    if fresh:
                        drafts.append(ChunkDraft(len(drafts), " ".join(buf), u.section_path, u.section_id, u.page_start, u.page_end))
                    buf = buf[-1:] if buf and len(buf[-1]) + len(s) + 1 <= MAX_CHARS else []
                    fresh = False
                buf.append(s)
                fresh = True
            if buf and fresh:
                drafts.append(ChunkDraft(len(drafts), " ".join(buf), u.section_path, u.section_id, u.page_start, u.page_end))
        else:
            cur.append(u)
    flush()
    return drafts


def chunk_id(document_id: str, d: ChunkDraft) -> str:
    return hashlib.sha1(f"{document_id}|{d.idx}|{d.text}".encode()).hexdigest()


def index_document(db: Session, document_id: str, embedder=None) -> dict:
    """Idempotent: deterministic ids, stale chunks removed, existing chunks/embeddings kept."""
    units = db.scalars(select(Unit).where(Unit.document_id == document_id).order_by(Unit.idx)).all()
    drafts = chunk_units(units)
    ids = {chunk_id(document_id, d): d for d in drafts}
    existing = {c.id: c for c in db.scalars(select(Chunk).where(Chunk.document_id == document_id))}
    stale = [cid for cid in existing if cid not in ids]
    if stale:
        db.execute(delete(Chunk).where(Chunk.id.in_(stale)))
    created = 0
    for cid, d in ids.items():
        if cid not in existing:
            db.add(Chunk(id=cid, document_id=document_id, idx=d.idx, page_start=d.page_start, page_end=d.page_end,
                         heading=d.heading, section_id=d.section_id, text=d.text))
            created += 1
    db.flush()
    embedded = 0
    if embedder is not None:
        todo = [c for c in db.scalars(select(Chunk).where(Chunk.document_id == document_id)) if c.embedding is None or c.embedding_model != embedder.name]
        if todo:
            vecs = embedder.embed([f"{c.heading}\n{c.text}" for c in todo])
            for c, v in zip(todo, vecs):
                c.embedding, c.embedding_model = v.astype(np.float32).tobytes(), embedder.name
            embedded = len(todo)
    return {"chunks": len(ids), "created": created, "removed": len(stale), "embedded": embedded}


def ensure_indexed(db: Session, document_id: str, embedder=None) -> dict | None:
    """Cheap per-request guard: re-chunk/re-embed only when something is missing (no chunks, or chunks lacking an
    embedding from the *current* embedder). Returns None when nothing had to be done."""
    total = db.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)) or 0
    if total:
        if embedder is None:
            return None
        missing = db.scalar(select(func.count()).select_from(Chunk).where(
            Chunk.document_id == document_id, (Chunk.embedding.is_(None)) | (Chunk.embedding_model != embedder.name))) or 0
        if not missing:
            return None
    return index_document(db, document_id, embedder)


@dataclass
class Hit:
    chunk: Chunk
    bm25: float = 0.0
    dense: float | None = None
    bm25_rank: int | None = None
    dense_rank: int | None = None
    score: float = 0.0
    coverage: float = 0.0  # share of distinct query terms present in the chunk
    wcoverage: float = 0.0  # same, weighted by corpus IDF (terms absent from the whole corpus weigh most)
    rerank: float | None = None
    meta: dict = field(default_factory=dict)


def search(db: Session, document_ids: list[str], query: str, k: int = 6, mode: str = "hybrid", embedder=None, rerank: bool = False) -> tuple[list[Hit], dict]:
    """mode: bm25 | dense | hybrid. Hybrid = Reciprocal Rank Fusion (k=60) of the BM25 and dense rankings,
    which needs no score calibration between the two systems."""
    chunks = db.scalars(select(Chunk).where(Chunk.document_id.in_(document_ids)).order_by(Chunk.document_id, Chunk.idx)).all()
    info = {"mode_requested": mode, "mode_used": mode, "chunks_searched": len(chunks), "notes": []}
    if not chunks:
        return [], info
    hits = {c.id: Hit(c) for c in chunks}
    q_terms = list(dict.fromkeys(content_terms(query)))

    dense_ok = False
    if mode in ("dense", "hybrid"):
        if embedder is None:
            info["notes"].append("Dense retrieval unavailable (no local embedding model); using BM25 only.")
        else:
            idx = [i for i, c in enumerate(chunks) if c.embedding is not None and c.embedding_model == embedder.name]
            if idx:
                M = np.stack([np.frombuffer(chunks[i].embedding, dtype=np.float32) for i in idx])
                sims = M @ embedder.embed([query])[0]
                for rank, j in enumerate(np.argsort(-sims)):
                    h = hits[chunks[idx[j]].id]; h.dense, h.dense_rank = float(sims[j]), rank + 1
                dense_ok = True
                if len(idx) < len(chunks):
                    info["notes"].append(f"Only {len(idx)}/{len(chunks)} chunks have embeddings from '{embedder.name}'; the rest rank by BM25 only.")
            else:
                info["notes"].append("Chunks are not embedded yet (or were embedded by a different model); using BM25 only.")
    if mode == "dense" and dense_ok:
        info["mode_used"] = "dense"
    elif mode == "hybrid" and dense_ok:
        info["mode_used"] = "hybrid"
    else:
        info["mode_used"] = "bm25"  # requested bm25, or honest degradation when dense is unavailable
    if info["mode_used"] in ("bm25", "hybrid"):
        bm = BM25([tokenize(c.text + " " + c.heading) for c in chunks])
        scores = bm.get_scores(tokenize(query))
        for rank, i in enumerate(np.argsort(-scores, kind="stable")):
            h = hits[chunks[i].id]; h.bm25, h.bm25_rank = float(scores[i]), rank + 1
    n = len(chunks)
    word_sets = {c.id: set(tokenize(c.text + " " + c.heading)) for c in chunks}
    idf = {t: float(np.log(1 + (n - df + 0.5) / (df + 0.5))) for t in q_terms for df in [sum(1 for w in word_sets.values() if t in w)]}
    total_w = sum(idf.values()) or 1.0
    for h in hits.values():
        if info["mode_used"] == "bm25":
            h.score = h.bm25
        elif info["mode_used"] == "dense":
            h.score = h.dense or 0.0
        else:
            h.score = sum(1.0 / (RRF_K + r) for r in (h.bm25_rank, h.dense_rank) if r)
        words = word_sets[h.chunk.id]
        h.coverage = (sum(1 for t in q_terms if t in words) / len(q_terms)) if q_terms else 0.0
        h.wcoverage = (sum(idf[t] for t in q_terms if t in words) / total_w) if q_terms else 0.0
    ranked = sorted(hits.values(), key=lambda h: -h.score)[: max(k * 3 if rerank else k, k)]
    if rerank:
        ranked = _rerank(query, ranked, info)
    return ranked[:k], info


_ce_lock = threading.Lock()
_ce_cache: dict = {}


def _cross_encoder(name: str):
    """Loaded once per process (a CrossEncoder load costs seconds and hundreds of MB); failures are cached too."""
    with _ce_lock:
        if name not in _ce_cache:
            try:
                from sentence_transformers import CrossEncoder
                _ce_cache[name] = CrossEncoder(name, device="cpu", local_files_only=not get_settings().allow_model_download)
            except Exception as exc:
                _ce_cache[name] = exc
        return _ce_cache[name]


def _rerank(query: str, hits: list[Hit], info: dict) -> list[Hit]:
    name = get_settings().rerank_model
    if not name:
        info["notes"].append("Reranking requested but RERANK_MODEL is not configured.")
        return hits
    ce = _cross_encoder(name)
    if isinstance(ce, Exception):
        info["notes"].append(f"Reranker unavailable ({type(ce).__name__}); using hybrid order.")
        return hits
    try:
        scores = ce.predict([(query, h.chunk.text) for h in hits])
        for h, sc in zip(hits, scores):
            h.rerank = float(sc)
        info["notes"].append(f"Reranked with {name}.")
        return sorted(hits, key=lambda h: -(h.rerank or 0))
    except Exception as exc:
        info["notes"].append(f"Reranker failed ({type(exc).__name__}); using hybrid order.")
        return hits
