"""Deterministic comparison engine: alignment pairs -> categorized, evidence-backed change records."""
import re
from collections import Counter
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Callable

import numpy as np
from rapidfuzz import fuzz

from app.services.normalization import DEFAULT, NormalizationConfig, critical_flags
from app.services.section_alignment import AlignConfig, Pair, align, prepare, term_weights
from app.services.types import ChangeRecord, DocUnit

FLAG_TEXT = {
    "date_changed": "a date", "percentage_changed": "a percentage", "amount_changed": "a monetary amount",
    "number_changed": "a number", "legal_reference_changed": "a legal reference", "identifier_changed": "an identifier/code",
    "negation_changed": "negation/exception wording", "obligation_strength_changed": "obligation strength (shall/may/must…)",
}


def inline_diff(old: str, new: str) -> dict:
    """Word-level diff spans for highlighting: {'old': [[op, text]...], 'new': [...]} with op in eq|del|ins."""
    tok = lambda s: re.findall(r"\s+|\w+(?:[.,/%-]\w+)*|[^\w\s]", s)  # noqa: E731
    a, b = tok(old), tok(new)
    sm = SequenceMatcher(None, a, b, autojunk=False)
    o_spans, n_spans = [], []
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal":
            o_spans.append(["eq", "".join(a[a0:a1])]); n_spans.append(["eq", "".join(b[b0:b1])])
        else:
            if a1 > a0: o_spans.append(["del", "".join(a[a0:a1])])
            if b1 > b0: n_spans.append(["ins", "".join(b[b0:b1])])
    return {"old": o_spans, "new": n_spans}


def _describe(flags: list[str]) -> str:
    named = [FLAG_TEXT[f] for f in flags if f in FLAG_TEXT]
    return f" High-risk difference detected in {', '.join(named)}." if named else ""


def _loc(u: DocUnit) -> str:
    return f"p.{u.page}" + (f" §{u.section_id}" if u.section_id else "")


def _record(pair: Pair, old: DocUnit, new: DocUnit, ncfg: NormalizationConfig) -> ChangeRecord:
    renumbered = (old.section_id or "") != (new.section_id or "") and bool(old.section_id or new.section_id)
    conf = round(pair.score, 3)
    exact_text = old.norm == new.norm
    key_equal = old.key == new.key

    if pair.review and pair.method == "semantic":
        return ChangeRecord("NEEDS_REVIEW", "semantic",
            f"Different wording but high semantic similarity (cosine {conf:.2f}) between {_loc(old)} and {_loc(new)}. "
            "Embeddings suggest the provision may have been reworded; this is a candidate match, not a confirmed one.",
            old, new, conf, ["semantic_candidate"] + critical_flags(old.body, new.body), inline_diff(old.body, new.body), "pending")
    if pair.relocated:
        flags = ["relocated"] + ([] if exact_text else ["text_changed"] + critical_flags(old.body, new.body))
        cat = "NEEDS_REVIEW" if pair.review else "POSSIBLY_RELOCATED"
        why = (f"{'Identical' if exact_text else 'Near-identical'} text found at a different position: {_loc(old)} → {_loc(new)} "
               f"(match score {conf:.2f}).")
        if pair.review:
            why += " Several equally good candidates exist (e.g. duplicated text), so the pairing is ambiguous."
        why += "" if exact_text else " The wording also changed." + _describe(flags)
        return ChangeRecord(cat, pair.method, why, old, new, conf, flags, None if exact_text else inline_diff(old.body, new.body),
                            "pending" if pair.review else "none")
    if exact_text or (ncfg.ignore_punctuation_case and key_equal):
        flags = []
        why = "Text is identical after whitespace, Unicode and line-break normalization."
        if renumbered:
            flags.append("renumbered")
            if ncfg.renumbering_is_change:
                return ChangeRecord("MODIFIED", pair.method, f"Content identical but the clause number changed ({old.section_id or '—'} → {new.section_id or '—'}).",
                                    old, new, 1.0, flags, inline_diff(old.text, new.text))
            why = f"Text unchanged; clause number changed ({old.section_id or '—'} → {new.section_id or '—'}). Check cross-references."
        return ChangeRecord("UNCHANGED", pair.method, why, old, new, 1.0, flags)
    flags = critical_flags(old.body, new.body)
    if key_equal:
        flags.append("punctuation_or_case_only")
        why = "Only punctuation or letter case differs. Punctuation can alter legal meaning, so this is reported rather than ignored."
        return ChangeRecord("MODIFIED", pair.method, why, old, new, conf, flags, inline_diff(old.body, new.body))
    if pair.review and pair.ambiguous:
        return ChangeRecord("NEEDS_REVIEW", pair.method,
            f"Ambiguous match (similarity {conf:.2f}) between {_loc(old)} and {_loc(new)}: another unmatched passage nearby is about as "
            "similar, so it is unclear which passage this provision became. A human should decide." + _describe(flags),
            old, new, conf, ["ambiguous_match"] + flags, inline_diff(old.body, new.body), "pending")
    if pair.review and pair.frame_only:
        return ChangeRecord("NEEDS_REVIEW", pair.method,
            f"Shared wording frame (similarity {conf:.2f}) between {_loc(old)} and {_loc(new)}, but a multi-word phrase was replaced "
            "by a different one: this may be the same provision with a new subject, or a different provision written from the "
            "same template. A human should decide." + _describe(flags),
            old, new, conf, ["low_confidence", "shared_frame_only"] + flags, inline_diff(old.body, new.body), "pending")
    if pair.review:
        return ChangeRecord("NEEDS_REVIEW", pair.method,
            f"Low-confidence match (similarity {conf:.2f}) between {_loc(old)} and {_loc(new)}. These passages may be the same provision "
            "heavily rewritten, or unrelated; a human should decide." + _describe(flags),
            old, new, conf, ["low_confidence"] + flags, inline_diff(old.body, new.body), "pending")
    if renumbered:
        flags.append("renumbered")
    return ChangeRecord("MODIFIED", pair.method,
        f"Aligned by {'exact section/heading + fuzzy text' if pair.method == 'fuzzy' else pair.method} (similarity {conf:.2f}); wording differs." + _describe(flags),
        old, new, conf, flags, inline_diff(old.body, new.body))


# ---- split / merge (1 -> 2, 2 -> 1) -------------------------------------------------------------------------------
SPLIT_COVER = 0.85  # confident: >= this share of the single unit's (idf-weighted) words is found in the two parts ...
SPLIT_PRECISION = 0.80  # ... and >= this share of the two parts' words comes from the single unit
SPLIT_REVIEW = 0.70  # both measures >= this (but not confident) => NEEDS_REVIEW
SPLIT_PART_MIN = 0.15  # each part must add >= this much coverage on its own (otherwise it is just an unrelated neighbour)
PART_MARKER = "\n[--- next passage ---]\n"


def _w(c: Counter, idf: dict[str, float]) -> float:
    return sum(n * idf.get(t, 1.0) for t, n in c.items())


def _split_score(single: DocUnit, p1: DocUnit, p2: DocUnit, idf: dict[str, float]) -> tuple[float, float, float]:
    """(coverage, precision, min marginal contribution) of `single` by the concatenation p1 + p2, on idf-weighted word
    multisets (template words weigh little, so two neighbours that merely share boilerplate do not cover a clause)."""
    s = Counter(single.key.split())
    ws = _w(s, idf)
    a, b = Counter(p1.key.split()), Counter(p2.key.split())
    both = a + b
    if not ws or not both:
        return 0.0, 0.0, 0.0
    cov = _w(s & both, idf) / ws
    prec = _w(s & both, idf) / _w(both, idf)
    contrib = min(cov - _w(s & b, idf) / ws, cov - _w(s & a, idf) / ws)
    return cov, prec, contrib


def _detect_splits(one: list[DocUnit], two: list[DocUnit], pairs: dict[int, Pair], free_one: set[int], free_two: set[int],
                   partner: dict[int, int], idf: dict[str, float], max_checks: int) -> list[tuple[int, int, int, bool]]:
    """Find units of `one` that correspond to two CONSECUTIVE units of `two`. Candidates: a unit of `one` that is
    unmatched or only fuzzily paired (never exact / relocated), and an adjacent pair (k, k+1) of `two` where every part is
    either unmatched or is that unit's current partner, at least one part being unmatched. Unmatched units of `one` are
    only tried against parts lying between the partners of their nearest matched neighbours (document order).
    Returns (i, k, k+1, confident)."""
    out, used_two, checks = [], set(), 0
    matched_sorted = sorted(partner)
    for i in range(len(one)):
        if i not in free_one and (i not in pairs or pairs[i].method != "fuzzy"):
            continue
        if i in pairs:
            j = pairs[i].j
            cands = [(j - 1, j), (j, j + 1)]
        else:
            prev = max((x for x in matched_sorted if x < i), default=None)
            nxt = min((x for x in matched_sorted if x > i), default=None)
            lo = partner[prev] if prev is not None else -1
            hi = partner[nxt] if nxt is not None else len(two)
            cands = [(k, k + 1) for k in range(lo + 1, hi - 1)]
        best = None
        for k1, k2 in cands:
            if k1 < 0 or k2 >= len(two) or k1 in used_two or k2 in used_two:
                continue
            ok = [k in free_two or (i in pairs and pairs[i].j == k) for k in (k1, k2)]
            if not all(ok) or not (k1 in free_two or k2 in free_two):
                continue
            checks += 1
            if checks > max_checks:
                return out
            cov, prec, contrib = _split_score(one[i], two[k1], two[k2], idf)
            if contrib < SPLIT_PART_MIN or min(cov, prec) < SPLIT_REVIEW:
                continue
            conf = cov >= SPLIT_COVER and prec >= SPLIT_PRECISION
            cand = (min(cov, prec), k1, k2, conf)
            if best is None or cand[0] > best[0]:
                best, ambiguous = cand, False
            elif best[0] - cand[0] < 0.03:
                ambiguous = True
        if best is not None:
            out.append((i, best[1], best[2], best[3] and not ambiguous))
            used_two.update((best[1], best[2]))
    return out


def _joined(p1: DocUnit, p2: DocUnit) -> DocUnit:
    """One evidence unit carrying BOTH parts (text separated by a marker line), keyed to the first part's index/page."""
    return replace(p1, text=p1.text + PART_MARKER + p2.text, body=p1.body + " " + p2.body,
                   norm=p1.norm + " " + p2.norm, key=p1.key + " " + p2.key, page_end=max(p2.page_end or p2.page, p1.page_end or p1.page))


def _split_record(single: DocUnit, p1: DocUnit, p2: DocUnit, confident: bool, merged: bool) -> ChangeRecord:
    joined = _joined(p1, p2)
    old, new = (joined, single) if merged else (single, joined)
    sim = fuzz.ratio(old.key, new.key) / 100.0
    flags = ["merged" if merged else "split"] + critical_flags(old.body, new.body)
    side = "old" if merged else "new"
    what = (f"Two consecutive old passages ({_loc(p1)} and {_loc(p2)}) were merged into one new passage ({_loc(single)})" if merged else
            f"One old passage ({_loc(single)}) was split into two consecutive new passages ({_loc(p1)} and {_loc(p2)})")
    why = (f"{what}: the {side} text shown is both parts joined, separated by a marker line (combined similarity {sim:.2f})."
           + ("" if confident else " The parts only partly account for the text, so a human should confirm.") + _describe(flags))
    if not confident:
        flags.insert(0, "low_confidence")
    return ChangeRecord("MODIFIED" if confident else "NEEDS_REVIEW", "fuzzy", why, old, new, round(sim, 3), flags,
                        inline_diff(old.body, new.body), "none" if confident else "pending")


def _apply_splits(old: list[DocUnit], new: list[DocUnit], pairs: list[Pair], records: list, ac: AlignConfig
                  ) -> tuple[list[Pair], set[int], set[int]]:
    """Replace MODIFIED+ADDED (split) and MODIFIED+REMOVED (merge) by one record carrying both parts. Appends the split /
    merge records to `records`; returns the pairs that were not consumed and the consumed old / new indices."""
    if not old or not new:
        return pairs, set(), set()
    idf = term_weights(old, new)
    cap = max(1, ac.max_dp_cells // 10)  # bounded work on huge leftovers
    by_old = {p.i: p for p in pairs}
    o2n = {p.i: p.j for p in pairs if not p.relocated}
    free_o, free_n = set(range(len(old))) - {p.i for p in pairs}, set(range(len(new))) - {p.j for p in pairs}
    drop, used_o, used_n = set(), set(), set()
    for i, k1, k2, conf in _detect_splits(old, new, by_old, free_o, free_n, o2n, idf, cap):
        if i in by_old:
            drop.add(id(by_old[i]))
        records.append(((k1, 0, 0), _split_record(old[i], new[k1], new[k2], conf, merged=False)))
        used_o.add(i); used_n.update((k1, k2))
    keep = [p for p in pairs if id(p) not in drop]
    # merges: the same search with the roles of the versions swapped (one NEW unit <- two consecutive OLD units)
    by_new = {p.j: Pair(p.j, p.i, p.method, p.score, p.review, p.relocated) for p in keep}
    n2o = {p.j: p.i for p in keep if not p.relocated}
    for j, k1, k2, conf in _detect_splits(new, old, by_new, free_n - used_n, free_o - used_o, n2o, idf, cap):
        if j in by_new:
            drop.add(id(next(p for p in keep if p.j == j)))
        records.append(((j, 0, 0), _split_record(new[j], old[k1], old[k2], conf, merged=True)))
        used_n.add(j); used_o.update((k1, k2))
    return [p for p in pairs if id(p) not in drop], used_o, used_n


def compare_units(
    old: list[DocUnit], new: list[DocUnit],
    ncfg: NormalizationConfig = DEFAULT, ac: AlignConfig | None = None,
    embed: Callable[[list[str]], np.ndarray] | None = None,
) -> tuple[list[ChangeRecord], list[str]]:
    old, new = prepare(old, ncfg), prepare(new, ncfg)
    pairs, notes = align(old, new, ncfg, ac, embed)
    records: list[tuple[tuple, ChangeRecord]] = []
    pairs, split_o, split_n = _apply_splits(old, new, pairs, records, ac or AlignConfig())
    matched_old = {p.i for p in pairs} | split_o
    matched_new = {p.j for p in pairs} | split_n
    old_to_new = {p.i: p.j for p in pairs if not p.relocated}
    for p in pairs:
        records.append(((p.j, 0, 0), _record(p, old[p.i], new[p.j], ncfg)))
    for j, u in enumerate(new):
        if j not in matched_new:
            records.append(((j, 0, 0), ChangeRecord(
                "ADDED", "none", f"No matching passage was found in the old document ({_loc(u)}); the closest candidates scored below the match thresholds.",
                None, u, None, [])))
    for i, u in enumerate(old):
        if i not in matched_old:
            prev = max((k for k in old_to_new if k < i), default=None)
            anchor = old_to_new[prev] if prev is not None else -1
            records.append(((anchor, 1, i), ChangeRecord(
                "REMOVED", "none", f"No matching passage was found in the new document for {_loc(u)}.", u, None, None, [])))
    records.sort(key=lambda t: t[0])
    return [r for _, r in records], notes


def summarize(records: list[ChangeRecord]) -> dict:
    counts = {c: 0 for c in ("ADDED", "REMOVED", "MODIFIED", "UNCHANGED", "POSSIBLY_RELOCATED", "NEEDS_REVIEW")}
    critical = 0
    for r in records:
        counts[r.category] += 1
        if any(f in FLAG_TEXT for f in r.flags):
            critical += 1
    return {"counts": counts, "total": len(records), "critical_changes": critical,
            "pending_review": sum(1 for r in records if r.review_status == "pending")}
