"""Provisions removed in BOTH of two independent comparison pairs.

Direction: A1 → A2 and B1 → B2. A provision is 'commonly removed' only if a REMOVED change in pair A is
matched to a REMOVED change in pair B by (exact normalized text) or (fuzzy similarity ≥ 0.85), or flagged as
a semantic candidate for human review. Absence from unrelated documents is never used as evidence."""
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import Change, Comparison
from app.services.embeddings import get_embedder
from app.services.normalization import match_key, normalize, split_numbering

FUZZY_MIN = 0.85
SEMANTIC_MIN = 0.80


def _removed(db: Session, comp_id: str) -> list[Change]:
    return list(db.scalars(select(Change).where(Change.comparison_id == comp_id, Change.category == "REMOVED").order_by(Change.seq)))


def _name(db: Session, c: Comparison) -> str:
    from app.models.tables import Document
    da, dbb = db.get(Document, c.doc_a_id), db.get(Document, c.doc_b_id)
    return f"{c.label_a}: {da.original_name if da else '?'} → {c.label_b}: {dbb.original_name if dbb else '?'}"


def common_removals(db: Session, a: Comparison, b: Comparison) -> dict:
    ra, rb = _removed(db, a.id), _removed(db, b.id)
    body = lambda c: split_numbering(c.old_text or "")[1]  # noqa: E731
    na = [normalize(body(c)) for c in ra]
    nb = [normalize(body(c)) for c in rb]
    ka, kb = [match_key(body(c)) for c in ra], [match_key(body(c)) for c in rb]
    used_b, out = set(), []
    for i, ca in enumerate(ra):
        best = (0.0, None, "")
        for j, cb in enumerate(rb):
            if j in used_b:
                continue
            if na[i] == nb[j]:
                s, m = 1.0, "exact"
            else:
                s, m = max(fuzz.ratio(ka[i], kb[j]), fuzz.token_sort_ratio(ka[i], kb[j])) / 100.0, "fuzzy"
            if s > best[0]:
                best = (s, j, m)
        if best[1] is not None and best[0] >= FUZZY_MIN:
            used_b.add(best[1])
            out.append((ca, rb[best[1]], best[2], best[0], "confirmed"))
    emb = get_embedder()
    rest_a = [(i, c) for i, c in enumerate(ra) if c.id not in {x[0].id for x in out}]
    rest_b = [(j, c) for j, c in enumerate(rb) if j not in used_b]
    if emb is not None and getattr(emb, "semantic", False) and rest_a and rest_b:
        S = emb.embed([body(c) for _, c in rest_a]) @ emb.embed([body(c) for _, c in rest_b]).T
        taken = set()
        for x, (_, ca) in enumerate(rest_a):
            y = int(S[x].argmax())
            if S[x, y] >= SEMANTIC_MIN and y not in taken:
                taken.add(y)
                out.append((ca, rest_b[y][1], "semantic", float(S[x, y]), "needs_review"))
    items = []
    for ca, cb, method, score, status in out:
        ev = lambda c, comp: {"change_id": c.id, "comparison_id": comp.id, "text": c.old_text, "page": c.old_page, "section": c.old_section}  # noqa: E731
        items.append({"status": status, "match_method": method, "similarity": round(score, 3),
                      "evidence_a": ev(ca, a), "evidence_b": ev(cb, b)})
    return {
        "direction": f"Removed in BOTH pair A ({_name(db, a)}) AND pair B ({_name(db, b)}); each is checked as old → new",
        "pair_a": {"comparison_id": a.id, "removed_total": len(ra)}, "pair_b": {"comparison_id": b.id, "removed_total": len(rb)},
        "items": items, "confirmed": sum(1 for i in items if i["status"] == "confirmed"),
        "needs_review": sum(1 for i in items if i["status"] == "needs_review"),
    }
