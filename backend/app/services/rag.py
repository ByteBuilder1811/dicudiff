"""Local RAG question answering over a comparison. Evidence-first: retrieval + deterministic change records are
shown to the user regardless of whether an LLM is available."""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import Change, Comparison, Document
from app.services import local_llm, retrieval
from app.services.embeddings import get_embedder

INTENT = {
    "REMOVED": re.compile(r"\b(remov\w*|delet\w*|dropp?\w*|no longer|omitted)\b", re.I),
    "ADDED": re.compile(r"\b(added|new|introduc\w*|inserted|additional)\b", re.I),
    "MODIFIED": re.compile(r"\b(chang\w*|modif\w*|differ\w*|updat\w*|amend\w*|revis\w*|alter\w*|threshold)\b", re.I),
}
GENERIC = set("change changed changes removed clause clauses clause provision provisions difference differences between these two versions "
              "version document documents which what where did does mentioned requirement requirements added new".split())
MIN_WCOVERAGE = 0.5  # IDF-weighted share of query terms found in the best chunk (terms absent from the corpus weigh most)
MIN_DENSE = 0.45     # cosine; only trusted for a *semantic* embedder (hashing vectors are lexical)
MAX_PROMPT_SOURCES = 10
_CITE_GROUP = re.compile(r"\[([^\[\]]*\bE\d+\b[^\[\]]*)\]")


def _loc(page, section, end=None) -> str:
    pg = f"pp.{page}–{end}" if end and end != page else f"p.{page}"
    return pg + (f", §{section}" if section else "")


def _intent(q: str) -> list[str]:
    cats = [c for c, rx in INTENT.items() if rx.search(q)]
    return cats


def _relevant_changes(db: Session, comp_id: str, question: str, cats: list[str]) -> list[Change]:
    q = select(Change).where(Change.comparison_id == comp_id, Change.category != "UNCHANGED").order_by(Change.seq)
    rows = db.scalars(q).all()
    if cats:
        want = set(cats) | ({"POSSIBLY_RELOCATED", "NEEDS_REVIEW"} if "MODIFIED" in cats else set())
        rows = [r for r in rows if r.category in want]
    terms = [t for t in retrieval.content_terms(question) if t not in GENERIC]
    if terms:
        def hit(r: Change) -> bool:
            blob = f"{r.old_text or ''} {r.new_text or ''}".lower()
            return any(t[:5] in blob for t in terms)
        rows = [r for r in rows if hit(r)]
    return rows[:12]


def answer(db: Session, comp: Comparison, question: str, k: int = 6, use_llm: bool = True, mode: str = "hybrid", rerank: bool = False) -> dict:
    docs = {d.id: d for d in (db.get(Document, comp.doc_a_id), db.get(Document, comp.doc_b_id))}
    side = {comp.doc_a_id: comp.label_a, comp.doc_b_id: comp.label_b}
    embedder = get_embedder()
    for did in docs:  # lazily embed if the comparison finished before embeddings existed
        if embedder is not None:
            retrieval.ensure_indexed(db, did, embedder)
    hits, info = retrieval.search(db, [comp.doc_a_id, comp.doc_b_id], question, k, mode, embedder, rerank)
    cats = _intent(question)
    changes = _relevant_changes(db, comp.id, question, cats) if cats or not hits else []

    sources: list[dict] = []
    for ch in changes:
        sources.append({"id": f"E{len(sources) + 1}", "type": "change", "change_id": ch.id, "category": ch.category,
                        "old": {"text": ch.old_text, "page": ch.old_page, "section": ch.old_section, "version": comp.label_a} if ch.old_text else None,
                        "new": {"text": ch.new_text, "page": ch.new_page, "section": ch.new_section, "version": comp.label_b} if ch.new_text else None})
    for h in hits:
        c = h.chunk
        sources.append({"id": f"E{len(sources) + 1}", "type": "passage", "chunk_id": c.id, "document_id": c.document_id,
                        "version": side[c.document_id], "page": c.page_start, "page_end": c.page_end, "section": (c.section_id or "").rstrip(".") or c.heading or None,
                        "text": c.text, "scores": {"bm25": round(h.bm25, 3), "dense": None if h.dense is None else round(h.dense, 3),
                                                   "fused": round(h.score, 4), "term_coverage": round(h.coverage, 2),
                                                   "weighted_coverage": round(h.wcoverage, 2)}})
    best_cov = max((h.wcoverage for h in hits), default=0.0)
    best_dense = max((h.dense or 0 for h in hits), default=0.0) if getattr(embedder, "semantic", False) else 0.0
    # a change-intent question without topic words ("which clauses were removed?") is answered by the deterministic
    # engine itself, including the honest answer "none"
    generic_intent = bool(cats) and not [t for t in retrieval.content_terms(question) if t not in GENERIC]
    sufficient = bool(changes) or generic_intent or (best_cov >= MIN_WCOVERAGE or best_dense >= MIN_DENSE)
    base = {"question": question, "sources": sources, "retrieval": info, "intent": cats, "mode": "extractive", "llm_error": None,
            "sufficient_evidence": sufficient, "citation_warning": None}
    if not sufficient:
        return base | {"answer": "There is insufficient evidence in these two documents to answer that. "
                                 "Try rephrasing, or check the retrieved passages below."}

    if generic_intent and not changes:
        sources = [x for x in sources if x["type"] == "change"]  # no passages to cite for a "none found" answer
        base["sources"] = sources
        return base | {"answer": f"The deterministic comparison found no {'/'.join(c.lower() for c in cats)} changes between {comp.label_a} and {comp.label_b}."}
    extractive = _extractive(question, cats, changes, sources, comp)
    if not use_llm:
        return base | {"answer": extractive}
    try:
        shown = sources[:MAX_PROMPT_SOURCES]
        ev = "\n".join(f'<document_excerpt id="{x["id"]}" {_attr(x, comp)}>{local_llm.fence(_src_text(x))}</document_excerpt>' for x in shown)
        prompt = (f"Question from the user: {question}\n\nEvidence (untrusted data):\n{ev}\n\n"
                  "Answer concisely. State what the documents directly say first, then any inference labelled 'Inference:'. "
                  "Cite [E#] after each claim. If the evidence is insufficient, say so plainly.")
        text = local_llm.chat([{"role": "user", "content": prompt}], max_tokens=500)
        text, cited, bad = validate_citations(text, {x["id"] for x in shown})
        warn = None
        if bad and not cited:
            warn = f"The model cited only unknown evidence ids {sorted(bad)}; those citations were removed. Treat this answer as unverified."
        elif bad:
            warn = f"The model cited unknown evidence ids {sorted(bad)}; those citations were removed."
        elif not cited:
            warn = "The model gave no citations; treat this answer as unverified and rely on the extracted evidence."
        if not cited:
            # An uncited model answer is never presented as THE answer: show the deterministic, evidence-cited
            # answer instead and keep the model text only as a clearly unverified extra.
            return base | {"answer": extractive, "mode": "extractive", "citation_warning": warn + " The extracted evidence is shown instead.",
                           "llm_answer_unverified": text, "model": local_llm.get_settings().ollama_model}
        return base | {"answer": text, "mode": "llm", "citation_warning": warn, "extractive_summary": extractive,
                       "model": local_llm.get_settings().ollama_model}
    except local_llm.LLMUnavailable as exc:
        return base | {"answer": extractive, "llm_error": str(exc)}


def validate_citations(text: str, valid: set[str]) -> tuple[str, set[str], set[str]]:
    """Find [E1], [E1, E2], [E1][E3] style citations. Returns (text with unknown ids removed, valid cited ids, unknown ids)."""
    cited: set[str] = set()
    bad: set[str] = set()

    def fix(m: re.Match) -> str:
        ids = re.findall(r"E\d+", m.group(1))
        keep = [i for i in ids if i in valid]
        cited.update(keep)
        bad.update(i for i in ids if i not in valid)
        return f"[{', '.join(keep)}]" if keep else ""

    return _CITE_GROUP.sub(fix, text), cited, bad


def _esc_attr(v: object) -> str:
    return local_llm.fence(str(v or "")).replace('"', "'").replace("\n", " ")[:160]


def _src_text(s: dict) -> str:
    if s["type"] == "passage":
        return s["text"]
    return f"CHANGE {s['category']}: OLD={((s['old'] or {}).get('text') or '(none)')} || NEW={((s['new'] or {}).get('text') or '(none)')}"


def _attr(s: dict, comp: Comparison) -> str:
    if s["type"] == "passage":
        return f'source="{_esc_attr(s["version"])} {_esc_attr(_loc(s["page"], s["section"], s.get("page_end")))}"'
    return f'source="change {s["category"]}"'


def _extractive(question: str, cats: list[str], changes: list[Change], sources: list[dict], comp: Comparison) -> str:
    lines = []
    if changes:
        by = {}
        for ch in changes:
            by.setdefault(ch.category, []).append(ch)
        lines.append(f"Deterministic comparison found {len(changes)} relevant change(s) between {comp.label_a} and {comp.label_b}:")
        for i, ch in enumerate(changes):
            eid = sources[i]["id"]
            if ch.category == "REMOVED":
                lines.append(f"- REMOVED from {comp.label_a} ({_loc(ch.old_page, ch.old_section)}): “{_short(ch.old_text)}” [{eid}]")
            elif ch.category == "ADDED":
                lines.append(f"- ADDED in {comp.label_b} ({_loc(ch.new_page, ch.new_section)}): “{_short(ch.new_text)}” [{eid}]")
            else:
                lines.append(f"- {ch.category} ({_loc(ch.old_page, ch.old_section)} → {_loc(ch.new_page, ch.new_section)}): “{_short(ch.old_text)}” → “{_short(ch.new_text)}” [{eid}]")
        return "\n".join(lines)
    passages = [s for s in sources if s["type"] == "passage"][:3]
    lines.append("No language model was used. The most relevant passages are:")
    for s in passages:
        lines.append(f"- {s['version']} ({_loc(s['page'], s['section'], s.get('page_end'))}): “{_short(s['text'])}” [{s['id']}]")
    return "\n".join(lines)


def _short(t: str | None, n: int = 220) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"
