"""Reproducible synthetic benchmark: generates PDF pairs with KNOWN changes, runs the real pipeline
(PDF -> extraction -> alignment -> diff -> chunking -> retrieval) and prints measured metrics.

  python scripts/benchmark.py --split fresh   # untouched held-out seeds 5000-5029 (evaluate ONCE, never tune on it)
  python scripts/benchmark.py --split test    # seeds 1000-1029: "previously evaluated" (results were seen during development)
  python scripts/benchmark.py --split dev     # development seeds 0-9 (used while tuning thresholds)
  python scripts/benchmark.py --embedding sentence-transformers   # use the real local model for dense/semantic
  python scripts/benchmark.py --show-misses   # print every gold change not reported in its gold category, with the record it landed in
  python scripts/benchmark.py --dump recs.json  # raw per-seed records + gold, for independent re-scoring
                                                 # (docs/benchmarks/recompute_metrics.py)

Splits: dev (0-9) was used to tune thresholds. test (1000-1029) was looked at while fixing the lexical similarity
function, so it is no longer a clean held-out set; it is reported as "previously evaluated". fresh (5000-5029) was
created afterwards and must only be run after all tuning is finished.

Metrics (see docs/benchmarks/AGENT_A_FINDINGS.md for the audit):
  strict    : a gold ADDED/REMOVED/MODIFIED change counts as found only if a record of exactly that category carries it.
              NEEDS_REVIEW records therefore count as misses (FN) for the strict metric.
  surfaced  : a gold ADDED/REMOVED change also counts as surfaced if it appears on the matching side of a NEEDS_REVIEW
              record (the system did not silently hide it; a human is asked). This is reported separately, never merged.
  review breakdown: every NEEDS_REVIEW record is classified against gold (true modified pair / removed x added pair / ...).
Everything is synthetic: results indicate behaviour on clean, digitally generated PDFs only."""
import argparse
import json
import os
import platform
import random
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.normalization import normalize, split_numbering  # noqa: E402  (pure module, no settings)

SPLITS = {
    "dev": [i for i in range(0, 10)],
    "test": [1000 + i for i in range(30)],  # previously evaluated
    "fresh": [5000 + i for i in range(30)],  # untouched held-out split
}

SUBJECTS = ["the institution", "the bank", "the reporting entity", "the service provider", "the regulated lender", "the intermediary", "the custodian", "the branch manager"]
ACTIONS = [("verify the identity of", "each applicant"), ("retain records relating to", "cash transactions"), ("report suspicious activity concerning", "linked accounts"),
           ("review the risk rating of", "high value customers"), ("notify the regulator about", "data breaches"), ("conduct an audit of", "outsourced vendors"),
           ("obtain written consent from", "joint account holders"), ("suspend access for", "dormant accounts"), ("disclose fees applicable to", "retail borrowers"),
           ("update the address details of", "existing customers"), ("screen against sanction lists", "all beneficiaries"), ("train staff handling", "complaints and grievances"),
           ("maintain segregation of", "client funds"), ("escalate disputes raised by", "senior citizens"), ("document the source of funds for", "large deposits"),
           ("assess the creditworthiness of", "small business owners"), ("publish a summary of", "interest rate changes"), ("encrypt stored copies of", "identity documents"),
           ("authenticate remote requests from", "corporate users"), ("approve exceptions requested by", "relationship managers")]
TAILS = ["within {n} days of onboarding", "at least once every {n} months", "before any amount above Rs. {n},000 is disbursed", "and keep a copy for {n} years",
         "no later than {n} working days after the event", "and the fee shall not exceed {n}% of the amount"]
HEADS = ["Scope and Applicability", "Customer Due Diligence", "Record Keeping", "Reporting Obligations", "Governance and Audit", "Fees and Charges"]


def clause(rng: random.Random, used: set) -> str:
    # NOTE (audit): the de-duplication below only rejects an exact duplicate, or a clause sharing the text before
    # " within"; clauses with other tails may share subject + action with an existing clause (near-duplicates).
    # Kept unchanged so that historical splits stay reproducible.
    while True:
        a, (v, o), t = rng.choice(SUBJECTS), rng.choice(ACTIONS), rng.choice(TAILS)
        text = f"{a.capitalize()} shall {v} {o} {t.format(n=rng.randint(3, 90))}."
        if text not in used and not any(text.split(" within")[0] == u.split(" within")[0] for u in used):
            used.add(text)
            return text


def modify(text: str, rng: random.Random) -> str:
    kind = rng.choice(["number", "negate", "modal"])
    if kind == "number" and re.search(r"\d+", text):
        return re.sub(r"\d+", lambda m: str(int(m.group()) + rng.randint(2, 9)), text, count=1)
    if kind == "modal" and " shall " in text:
        return text.replace(" shall ", " may ", 1)
    return text.replace(" shall ", " shall not ", 1)


def build_pair(seed: int):
    rng, used = random.Random(seed), set()
    sections = [(h, [clause(rng, used) for _ in range(rng.randint(3, 5))]) for h in rng.sample(HEADS, 4)]
    new = [(h, list(cs)) for h, cs in sections]
    truth = {"added": [], "removed": [], "modified": [], "moved": []}
    flat = [(si, ci) for si, (_, cs) in enumerate(sections) for ci in range(len(cs))]
    picks = rng.sample(flat, 6)
    for (si, ci) in picks[:2]:
        truth["modified"].append((sections[si][1][ci], None))
    for (si, ci) in picks[2:4]:
        truth["removed"].append(sections[si][1][ci])
    for (si, ci) in picks[:2]:
        old = sections[si][1][ci]; newt = modify(old, rng); new[si][1][ci] = newt
        truth["modified"][truth["modified"].index((old, None))] = (old, newt)
    for (si, ci) in picks[2:4]:
        new[si][1][ci] = None
    for si in rng.sample(range(4), 2):
        t = clause(rng, used); new[si][1].insert(rng.randint(0, len(new[si][1])), t); truth["added"].append(t)
    new = [(h, [c for c in cs if c]) for h, cs in new]
    return sections, new, truth


def render(path: Path, secs, renumber_shift=0, lpp=44):
    from app.services.synthetic import make_pdf

    blocks = []
    for i, (h, cs) in enumerate(secs, 1):
        blocks.append(("h1", f"{i + renumber_shift}. {h}"))
        for j, c in enumerate(cs, 1):
            blocks.append(("p", f"{i + renumber_shift}.{j} {c}"))
    return make_pdf(path, blocks, lines_per_page=lpp)


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 3), round(r, 3), round(f, 3)


def _body(t: str | None) -> str:
    return normalize(split_numbering(t or "")[1]) if t else ""


def score_pair(recs: list[dict], truth: dict, old_sections) -> dict:
    """Pure scoring of one pair. recs: dicts with category/old_text/new_text. Multiset (Counter) semantics so that a
    duplicated record is a false positive, not silently collapsed."""
    ga = Counter(normalize(t) for t in truth["added"])
    gr = Counter(normalize(t) for t in truth["removed"])
    gm = Counter((normalize(a), normalize(b)) for a, b in truth["modified"])
    fa = Counter(_body(r["new_text"]) for r in recs if r["category"] == "ADDED")
    fr = Counter(_body(r["old_text"]) for r in recs if r["category"] == "REMOVED")
    fm = Counter((_body(r["old_text"]), _body(r["new_text"])) for r in recs if r["category"] == "MODIFIED")
    review = [r for r in recs if r["category"] == "NEEDS_REVIEW"]
    rev_new = Counter(_body(r["new_text"]) for r in review)
    rev_old = Counter(_body(r["old_text"]) for r in review)
    c = Counter({k: 0 for k in ("review_true_modified_pair", "review_removed_x_added", "review_other")})
    for name, g, f in (("add", ga, fa), ("rem", gr, fr), ("mod", gm, fm)):
        c[f"{name}_tp"] += sum((g & f).values()); c[f"{name}_fp"] += sum((f - g).values()); c[f"{name}_fn"] += sum((g - f).values())
    # surfaced = strict TP + gold items found on the corresponding side of a NEEDS_REVIEW record
    c["add_surfaced"] = sum((ga & (fa + rev_new)).values())
    c["rem_surfaced"] = sum((gr & (fr + rev_old)).values())
    c["mod_surfaced"] = sum((gm & (fm + Counter((_body(r["old_text"]), _body(r["new_text"])) for r in review))).values())
    for r in review:
        o, n = _body(r["old_text"]), _body(r["new_text"])
        if (o, n) in gm:
            c["review_true_modified_pair"] += 1
        elif o in gr and n in ga:
            c["review_removed_x_added"] += 1
        else:
            c["review_other"] += 1
    c["review"] = len(review)
    changed_old = set(gr) | {a for a, _ in gm}
    unchanged = [normalize(x) for _, cs in old_sections for x in cs if normalize(x) not in changed_old]
    unc_found = Counter(_body(r["old_text"]) for r in recs if r["category"] == "UNCHANGED" and _body(r["old_text"]) == _body(r["new_text"]))
    c["unc_n"] = len(unchanged); c["unc_ok"] = sum(1 for x in unchanged if unc_found[x] > 0)
    heads = [normalize(h) for h, _ in old_sections]
    c["head_n"] = len(heads); c["head_ok"] = sum(1 for h in heads if unc_found[h] > 0)
    return dict(c)


def misses(recs: list[dict], truth: dict) -> list[str]:
    """Human-readable list of gold changes not reported in their gold category, with the record they landed in."""
    out = []
    def where(side, text):
        hits = [r for r in recs if _body(r[side]) == text]
        return "; ".join(f"{r['category']}[{r.get('match_method')}, conf={r.get('confidence')}] old={r['old_text']!r} new={r['new_text']!r}" for r in hits) or "NOT IN ANY RECORD"
    for t in truth["added"]:
        if not any(r["category"] == "ADDED" and _body(r["new_text"]) == normalize(t) for r in recs):
            out.append(f"  gold ADDED   {t!r}\n    -> {where('new_text', normalize(t))}")
    for t in truth["removed"]:
        if not any(r["category"] == "REMOVED" and _body(r["old_text"]) == normalize(t) for r in recs):
            out.append(f"  gold REMOVED {t!r}\n    -> {where('old_text', normalize(t))}")
    for a, b in truth["modified"]:
        if not any(r["category"] == "MODIFIED" and _body(r["old_text"]) == normalize(a) and _body(r["new_text"]) == normalize(b) for r in recs):
            out.append(f"  gold MODIFIED {a!r} -> {b!r}\n    -> {where('old_text', normalize(a))}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=sorted(SPLITS), default="test")
    ap.add_argument("--pairs", type=int, default=30)
    ap.add_argument("--embedding", default="hashing", choices=["hashing", "sentence-transformers", "none"])
    ap.add_argument("--out", default="")
    ap.add_argument("--dump", default="", help="write raw per-seed records + gold to this JSON file")
    ap.add_argument("--show-misses", action="store_true")
    args = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="docudiff-bench-")
    os.environ.update(DATA_DIR=tmp, EMBEDDING_BACKEND=args.embedding, OLLAMA_URL="http://127.0.0.1:9")

    import numpy as np
    from sqlalchemy import select

    from app.core.db import init_engine, session_scope
    from app.models.tables import Change, Comparison
    from app.services import pipeline, retrieval
    from app.services.embeddings import get_embedder

    seeds = SPLITS[args.split][: args.pairs]
    init_engine(f"sqlite:///{tmp}/bench.db")
    emb = get_embedder()
    tot: Counter = Counter()
    times, ret = [], {m: {"hit": {1: 0, 3: 0, 5: 0}, "rr": 0.0, "n": 0} for m in ("bm25", "dense", "hybrid")}
    cite_ok = cite_n = 0
    dump = []
    for seed in seeds:
        old_s, new_s, truth = build_pair(seed)
        d = Path(tmp) / f"s{seed}"; d.mkdir()
        # different line budgets per version => different page breaks
        pa, pb = render(d / "a.pdf", old_s, lpp=44), render(d / "b.pdf", new_s, lpp=38)
        t0 = time.perf_counter()
        with session_scope() as db:
            da, _ = pipeline.ingest_pdf(db, pa, "a.pdf", "A"); db_, _ = pipeline.ingest_pdf(db, pb, "b.pdf", "B")
            c = Comparison(doc_a_id=da.id, doc_b_id=db_.id, options={"semantic": args.embedding == "sentence-transformers"})
            db.add(c); db.flush(); cid = c.id; idb = db_.id
        pipeline.run_comparison(cid)
        times.append(time.perf_counter() - t0)
        with session_scope() as db:
            rows = db.scalars(select(Change).where(Change.comparison_id == cid).order_by(Change.seq)).all()
            assert db.get(Comparison, cid).status == "completed"
            recs = [dict(category=r.category, match_method=r.match_method, confidence=r.confidence, old_text=r.old_text, new_text=r.new_text,
                         old_page=r.old_page, new_page=r.new_page, old_section=r.old_section, new_section=r.new_section,
                         flags=r.flags, explanation=r.explanation, review_status=r.review_status) for r in rows]
            tot.update(score_pair(recs, truth, old_s))
            if args.dump:
                dump.append({"seed": seed, "old_sections": old_s, "truth": truth, "records": recs})
            if args.show_misses:
                m = misses(recs, truth)
                if m:
                    print(f"seed {seed}:", *m, sep="\n", file=sys.stderr)
            # --- retrieval: query from the clause's verb phrase + object, gold = chunk containing the clause (version B doc)
            # NOTE (audit): the query is copied verbatim from the gold clause, so lexical retrieval is favoured.
            if emb is not None:
                retrieval.index_document(db, idb, emb)
            for _, cs in new_s:
                for ctext in cs[:2]:
                    m = re.match(r"The? .*? shall (.*?) (within|at least|before|no later|and)", ctext, re.I)
                    if not m:
                        continue
                    query = f"what must be done to {m.group(1)}"
                    for mode in ret:
                        if mode == "dense" and emb is None:
                            continue
                        hits, _ = retrieval.search(db, [idb], query, 5, mode, emb)
                        ranks = [i for i, h in enumerate(hits, 1) if normalize(ctext) in normalize(h.chunk.text)]
                        ret[mode]["n"] += 1
                        for k in (1, 3, 5):
                            ret[mode]["hit"][k] += int(bool(ranks) and ranks[0] <= k)
                        ret[mode]["rr"] += 1 / ranks[0] if ranks else 0
                        if mode == "hybrid" and hits:
                            top = next((h for h in hits if normalize(ctext) in normalize(h.chunk.text)), None)
                            cite_n += 1
                            truepage = next((r["new_page"] for r in recs if r["new_text"] and normalize(ctext) in normalize(r["new_text"])), None)
                            cite_ok += int(top is not None and truepage is not None and top.chunk.page_start <= truepage <= top.chunk.page_end)

    def block(name):
        p, r, f = prf(tot[f"{name}_tp"], tot[f"{name}_fp"], tot[f"{name}_fn"])
        gold = tot[f"{name}_tp"] + tot[f"{name}_fn"]
        return {"precision": p, "recall": r, "f1": f, "tp": tot[f"{name}_tp"], "fp": tot[f"{name}_fp"], "fn": tot[f"{name}_fn"],
                "surfaced_recall_incl_needs_review": round(tot[f"{name}_surfaced"] / max(1, gold), 3)}

    res = {
        "split": args.split, "seeds": f"{seeds[0]}-{seeds[-1]}", "pairs": len(seeds),
        "embedding_backend": getattr(emb, "name", "none"), "semantic_embeddings": bool(getattr(emb, "semantic", False)),
        "added": block("add"), "removed": block("rem"), "modified": block("mod"),
        "alignment_accuracy_unchanged_clauses": round(tot["unc_ok"] / max(1, tot["unc_n"]), 3), "unchanged_clauses_n": tot["unc_n"],
        "headings_unchanged_accuracy": round(tot["head_ok"] / max(1, tot["head_n"]), 3),
        "needs_review": {"count": tot["review"], "true_modified_pair": tot["review_true_modified_pair"],
                         "removed_x_added_pair": tot["review_removed_x_added"], "other": tot["review_other"]},
        "retrieval": {m: ({"n": v["n"], **{f"recall@{k}": round(v["hit"][k] / v["n"], 3) for k in (1, 3, 5)}, "mrr": round(v["rr"] / v["n"], 3)} if v["n"] else "unavailable") for m, v in ret.items()},
        "citation_page_correctness": round(cite_ok / max(1, cite_n), 3),
        "end_to_end_seconds_per_pair": {"mean": round(float(np.mean(times)), 3), "p95": round(float(np.percentile(times, 95)), 3)},
        "machine": f"{platform.machine()} {platform.system()} py{platform.python_version()}",
    }
    print(json.dumps(res, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=2))
    if args.dump:
        Path(args.dump).write_text(json.dumps(dump, indent=1))


if __name__ == "__main__":
    main()
