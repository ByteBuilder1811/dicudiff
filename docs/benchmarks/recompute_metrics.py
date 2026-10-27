"""Independent re-scoring of a benchmark dump (``python scripts/benchmark.py --dump FILE``).

Deliberately shares NO code with scripts/benchmark.py (stdlib only, its own text normalisation, explicit loops instead
of Counter arithmetic) so that the benchmark's own arithmetic can be cross-checked.

  python docs/benchmarks/recompute_metrics.py docs/benchmarks/fresh_hashing_records.json [result.json]

Views reported for ADDED / REMOVED / MODIFIED:
  strict   : a gold change is a TP only if a record of the same category carries it (NEEDS_REVIEW = miss).
  surfaced : NEEDS_REVIEW records that carry the gold change on the right side count as TP ("sent to a human").
Precision is identical in both views: a NEEDS_REVIEW record is never counted as a false ADDED/REMOVED/MODIFIED.
"""
import json
import re
import sys
import unicodedata

_NUM = re.compile(r"^\s*\d{1,3}(?:\.\d{1,3})*[.)]?\s+")


def canon(text):
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"\s+", " ", t).strip()
    return _NUM.sub("", t, count=1)


def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3), "tp": tp, "fp": fp, "fn": fn}


def score_seed(recs, truth):
    out = {}
    gold = {
        "added": [("", canon(t)) for t in truth["added"]],
        "removed": [(canon(t), "") for t in truth["removed"]],
        "modified": [(canon(a), canon(b)) for a, b in truth["modified"]],
    }
    cat = {"added": "ADDED", "removed": "REMOVED", "modified": "MODIFIED"}

    def key(kind, r):
        o, n = canon(r["old_text"]), canon(r["new_text"])
        return ("", n) if kind == "added" else (o, "") if kind == "removed" else (o, n)

    for kind in ("added", "removed", "modified"):
        found = [key(kind, r) for r in recs if r["category"] == cat[kind]]
        review = [key(kind, r) for r in recs if r["category"] == "NEEDS_REVIEW"]
        g_left = list(gold[kind])
        tp = 0
        unmatched_found = []
        for f in found:
            if f in g_left:
                g_left.remove(f)
                tp += 1
            else:
                unmatched_found.append(f)
        fn_strict = len(g_left)
        surfaced_extra = 0
        for g in list(g_left):
            if g in review:
                review.remove(g)
                surfaced_extra += 1
        out[kind] = {"tp": tp, "fp": len(unmatched_found), "fn": fn_strict, "surfaced_extra": surfaced_extra}
    out["needs_review"] = sum(1 for r in recs if r["category"] == "NEEDS_REVIEW")
    return out


def score_dump(dump):
    tot = {k: {"tp": 0, "fp": 0, "fn": 0, "surfaced_extra": 0} for k in ("added", "removed", "modified")}
    review = 0
    per_seed_misses = {}
    for item in dump:
        s = score_seed(item["records"], item["truth"])
        review += s["needs_review"]
        for k in tot:
            for m in tot[k]:
                tot[k][m] += s[k][m]
            if s[k]["fn"]:
                per_seed_misses.setdefault(item["seed"], []).append(f"{k}: {s[k]['fn']} missed ({s[k]['surfaced_extra']} of them in NEEDS_REVIEW)")
    res = {"pairs": len(dump), "needs_review_records": review, "misses_by_seed": per_seed_misses}
    for k, v in tot.items():
        res[k] = {"strict": _prf(v["tp"], v["fp"], v["fn"]),
                  "surfaced": _prf(v["tp"] + v["surfaced_extra"], v["fp"], v["fn"] - v["surfaced_extra"])}
    return res


if __name__ == "__main__":
    dump = json.load(open(sys.argv[1]))
    res = score_dump(dump)
    print(json.dumps(res, indent=2))
    if len(sys.argv) > 2:  # compare with the benchmark's own numbers
        bench = json.load(open(sys.argv[2]))
        ok = True
        for k, bk in (("added", "added"), ("removed", "removed"), ("modified", "modified")):
            for m in ("precision", "recall", "f1"):
                if res[k]["strict"][m] != bench[bk][m]:
                    ok = False
                    print(f"MISMATCH {k}.{m}: recompute={res[k]['strict'][m]} benchmark={bench[bk][m]}")
            if res[k]["surfaced"]["recall"] != bench[bk]["surfaced_recall_incl_needs_review"]:
                ok = False
                print(f"MISMATCH {k}.surfaced_recall: recompute={res[k]['surfaced']['recall']} benchmark={bench[bk]['surfaced_recall_incl_needs_review']}")
        print("benchmark arithmetic agrees with independent recompute" if ok else "DISAGREEMENT (see above)")
        sys.exit(0 if ok else 1)
