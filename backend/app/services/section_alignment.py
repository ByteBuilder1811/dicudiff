"""Layered paragraph/section alignment.

Layer 1  exact normalized-text equality, order preserving (difflib LCS over unit keys; handles duplicates)
Layer 1b identical text at a different position (exact relocation)
Layer 2  order-preserving fuzzy alignment *between* the layer-1 anchors (Needleman-Wunsch style DP over
         lexical similarity, with a bonus for equal section ids / kinds)
Layer 3  (pass 2) global relocation search over leftovers: exact => POSSIBLY_RELOCATED, fuzzy => POSSIBLY_RELOCATED
Layer 4  optional local-embedding pass over leftovers: only ever yields NEEDS_REVIEW, never a silent match
Anything left is a true ADDED / REMOVED.

Lexical similarity (``lexical_similarity``) is the geometric mean of
  * a character score  max(ratio, 0.97 * token_sort_ratio)   (robust to small edits / typos), and
  * a word score       multiset Dice overlap of word tokens   (how many *words* are shared).
Character ratios alone are a poor discriminator for prose: two unrelated English sentences of similar length
routinely score 0.45-0.60 on ``fuzz.ratio`` purely from shared letters/stop-words, which sat right on the
NEEDS_REVIEW floor and made unrelated clauses (a deleted clause next to an inserted one) pair up. Requiring word
overlap too pushes unrelated prose to ~0.3-0.45 while a one-word edit stays >0.85.
Very short keys (< 20 chars, e.g. headings) are damped because both measures are noisy there.
Inside ``align`` the word score is weighted by distinctive-term weights (``term_weights``: log(1 + N/df), df counted
per version), so overlap on template/boilerplate words that occur in many clauses counts less than overlap on words
specific to one provision (AlignConfig.distinctive_terms; on synthetic dev seeds this cut unrelated clause pairs
scoring >= 0.50 by about a third while true one-token edits stayed >= 0.93).

Ambiguity in Layer 2: if an unpaired unit in the same gap is within ``ambiguity_margin`` of the chosen partner's raw
similarity, the pairing was decided by the section bonus or by order rather than by the text, so it is NEEDS_REVIEW
(``Pair.ambiguous``) instead of a confident MODIFIED.

Shared template frame (``phrase_swap``): a pair that clears fuzzy_modified but whose word diff replaces a multi-word
phrase with a different multi-word phrase (>= 3 words substituted, word Dice < 0.9) is NEEDS_REVIEW
(``Pair.frame_only``), not a confident MODIFIED. Insertions/deletions and one-word substitutions never trigger it.
The section bonus is scaled by ``distinctive_share`` (idf-weighted / plain word Dice), ramped from 0 at
AlignConfig.bonus_share_floor to full at 1.0, so an equal clause number only helps when the shared wording is
provision-specific rather than template vocabulary.
Split / merge (1 -> 2 consecutive, 2 -> 1) detection runs afterwards in diff_engine.compare_units.

Thresholds (AlignConfig) are applied to the *ranking* score = lexical + 0.01 (same kind) + section bonus:
  fuzzy_modified  >= : confident pairing (MODIFIED)
  fuzzy_review    >= and < fuzzy_modified: paired but NEEDS_REVIEW; below: not paired (ADDED/REMOVED)
  relocation_min  minimum ranking score for the global (out-of-order) relocation pass
The score stored on a Pair (and reported as confidence) is the raw lexical similarity without bonuses, so a
modified clause is never reported with a misleading 1.00 just because its section number matched.
"""
import bisect
import time
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable

import numpy as np
from rapidfuzz import fuzz

from app.services.normalization import DEFAULT, NormalizationConfig, match_key, normalize, split_numbering
from app.services.types import DocUnit


@dataclass
class AlignConfig:
    fuzzy_modified: float = 0.75  # >= : confident modification (see module docstring for the score definition)
    fuzzy_review: float = 0.50  # >= and < modified: NEEDS_REVIEW (low confidence)
    relocation_min: float = 0.88  # global pass threshold
    ambiguity_margin: float = 0.03  # runner-up within this margin => NEEDS_REVIEW
    semantic_min: float = 0.78  # cosine threshold for rewording candidates
    section_bonus: float = 0.08
    # The section bonus is multiplied by a ramp of distinctive_share: 0 at <= bonus_share_floor, full at 1.0. On dev seeds
    # same-slot template pairs had share 0.40-0.70 and true correspondences >= 0.95 (see SPLIT_MERGE_NOTES.md).
    bonus_share_floor: float = 0.70
    distinctive_terms: bool = True  # weight the word-overlap score by document-level term rarity (see term_weights)
    max_dp_cells: int = 1_000_000  # guard against huge gaps; fall back to a windowed greedy (bounded work + memory)
    deadline: float | None = None  # time.monotonic() value; align() raises AlignTimeout once it is passed


class AlignTimeout(Exception):
    """align() ran past AlignConfig.deadline (cooperative: checked inside every O(n*m) loop)."""


def _check(ac: "AlignConfig") -> None:
    if ac.deadline is not None and time.monotonic() > ac.deadline:
        raise AlignTimeout("alignment exceeded its time budget")


def _near(sorted_idx: list[int], x: int, k: int) -> list[int]:
    """At most ~2k entries of sorted_idx closest (by position) to x."""
    p = bisect.bisect_left(sorted_idx, x)
    return sorted_idx[max(0, p - k): p + k]


@dataclass
class Pair:
    i: int  # old index
    j: int  # new index
    method: str  # exact | fuzzy | relocated_exact | relocated_fuzzy | semantic
    score: float
    review: bool = False  # ambiguous / low confidence
    relocated: bool = False
    ambiguous: bool = False  # review because a rival candidate is about as similar (not because the score is low)
    frame_only: bool = False  # review because only a shared template frame matches (a multi-word phrase was swapped)


def prepare(units: list[DocUnit], cfg: NormalizationConfig = DEFAULT) -> list[DocUnit]:
    for k, u in enumerate(units):
        u.idx = k
        num, body = split_numbering(u.text)
        if num:
            u.section_id = num
        u.body = body
        u.norm = normalize(body, cfg)
        u.key = match_key(body)
    return units


def term_weights(old: list[DocUnit], new: list[DocUnit]) -> dict[str, float]:
    """Distinctive-term weights: w = log(1 + N / df), with df counted PER VERSION (max over the two versions) and
    N = max(len(old), len(new)). Words that occur in many units of a version (template / boilerplate vocabulary such
    as "shall", "the", "and keep a copy for") weigh little; words specific to one provision weigh more. Counting df per
    version matters: a word shared by an old clause and its new counterpart then has df=1, not 2, so true
    correspondences are not penalised. Purely corpus-statistical: no word lists, no special-cased strings."""
    dfs = []
    for units in (old, new):
        df: Counter = Counter()
        for u in units:
            df.update(set(u.key.split()))
        dfs.append(df)
    n = max(1, len(old), len(new))
    return {w: float(np.log1p(n / max(dfs[0][w], dfs[1][w]))) for w in set(dfs[0]) | set(dfs[1])}


def _word_dice(a: str, b: str, idf: dict[str, float] | None = None) -> float:
    ca, cb = Counter(a.split()), Counter(b.split())
    if idf is None:
        total = sum(ca.values()) + sum(cb.values())
        return 2.0 * sum((ca & cb).values()) / total if total else 1.0
    dflt: list[float] = []  # max weight, computed only if a token is missing (it was O(vocabulary) on EVERY call)

    def w(c: Counter) -> float:
        tot = 0.0
        for t, n in c.items():
            v = idf.get(t)
            if v is None:
                if not dflt:
                    dflt.append(max(idf.values(), default=1.0))
                v = dflt[0]
            tot += n * v
        return tot
    total = w(ca) + w(cb)
    return 2.0 * w(ca & cb) / total if total else 1.0


def lexical_similarity(a: DocUnit, b: DocUnit, idf: dict[str, float] | None = None) -> float:
    """Raw text similarity in [0, 1] (no section/kind bonus). See module docstring."""
    if not a.key and not b.key:
        return 1.0
    if a.key == b.key:
        return 1.0
    r = fuzz.ratio(a.key, b.key) / 100.0
    ts = fuzz.token_sort_ratio(a.key, b.key) / 100.0 * 0.97
    s = (max(r, ts) * _word_dice(a.key, b.key, idf)) ** 0.5
    short = min(len(a.key), len(b.key))
    if short < 20:  # both measures are noisy on very short strings (headings)
        s *= 0.7 + 0.3 * short / 20
    return s


def distinctive_share(a: DocUnit, b: DocUnit, idf: dict[str, float] | None) -> float:
    """How much of the shared word mass survives distinctive-term weighting: idf-weighted Dice / plain Dice, in [0, 1].
    ~1 when the shared words are provision-specific (or when the document is too small for idf to say anything);
    ~0.6 when the overlap is mostly template vocabulary that recurs across many clauses ("shall ... keep a copy for")."""
    if idf is None:
        return 1.0
    plain = _word_dice(a.key, b.key)
    return min(1.0, _word_dice(a.key, b.key, idf) / plain) if plain > 0 else 0.0


def phrase_swap(a: DocUnit, b: DocUnit) -> bool:
    """True when the edit replaces a multi-word phrase with a *different* multi-word phrase (word-level diff hunk with
    >= 2 words on both sides) and at least 3 words are substituted overall, while overall word overlap is < 0.9.
    That is the signature of a shared template frame carrying different subject matter ("report on cyber security
    incidents ... every quarter" vs "report on unclaimed deposits ... every year"), not of a localized edit: inserting
    or deleting words ("not", "unless exempted") and one-word substitutions (30->15, shall->may) never trigger it."""
    wa, wb = a.key.split(), b.key.split()
    if _word_dice(a.key, b.key) >= 0.9:
        return False
    sub = big = 0
    for op, a0, a1, b0, b1 in SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if op == "replace":
            k = min(a1 - a0, b1 - b0)
            sub += k
            big = max(big, k)
    return big >= 2 and sub >= 3


def similarity(a: DocUnit, b: DocUnit, bonus: float = 0.0, idf: dict[str, float] | None = None,
               share_floor: float = 0.70) -> float:
    """Ranking score: lexical similarity + small same-kind bonus + section-id bonus (capped at 1).
    The section bonus is scaled by ``distinctive_share``: an equal clause number is evidence of correspondence only
    in proportion to how much of the shared wording is provision-specific rather than template frame."""
    s = lexical_similarity(a, b, idf)
    if a.kind == b.kind:
        s += 0.01
    if bonus and a.section_id and a.section_id == b.section_id:
        s += bonus * max(0.0, (distinctive_share(a, b, idf) - share_floor) / (1.0 - share_floor))
    return min(s, 1.0)


def _exact_anchors(old: list[DocUnit], new: list[DocUnit], cfg: NormalizationConfig) -> list[tuple[int, int]]:
    ka = [(u.key if cfg.ignore_punctuation_case else u.norm) for u in old]
    kb = [(u.key if cfg.ignore_punctuation_case else u.norm) for u in new]
    sm = SequenceMatcher(None, ka, kb, autojunk=False)
    return [(blk.a + k, blk.b + k) for blk in sm.get_matching_blocks() for k in range(blk.size)]


def _dp_align(old: list[DocUnit], new: list[DocUnit], oi: list[int], nj: list[int], ac: AlignConfig,
              idf: dict[str, float] | None = None) -> list[tuple[int, int, float]]:
    """Order-preserving max-total-similarity matching inside one gap."""
    n, m = len(oi), len(nj)
    if n == 0 or m == 0:
        return []
    if n * m > ac.max_dp_cells:  # windowed greedy fallback: never builds the n*m matrix (work/memory <= max_dp_cells)
        half = max(1, ac.max_dp_cells // (2 * n))
        out, used = [], set()
        for a in range(n):
            _check(ac)
            c = a * m // n
            best_b, best_s = -1, -1.0
            for b in range(max(0, c - half), min(m, c + half)):
                s = similarity(old[oi[a]], new[nj[b]], ac.section_bonus, idf, ac.bonus_share_floor)
                if s > best_s:
                    best_b, best_s = b, s
            if best_b >= 0 and best_s >= ac.fuzzy_review and best_b not in used:
                used.add(best_b)
                out.append((oi[a], nj[best_b], float(best_s)))
        return out
    sim = np.zeros((n, m))
    for a in range(n):
        _check(ac)
        for b in range(m):
            sim[a, b] = similarity(old[oi[a]], new[nj[b]], ac.section_bonus, idf, ac.bonus_share_floor)
    ok = sim >= ac.fuzzy_review
    dp = np.zeros((n + 1, m + 1))
    for a in range(1, n + 1):
        _check(ac)
        for b in range(1, m + 1):
            best = max(dp[a - 1, b], dp[a, b - 1])
            if ok[a - 1, b - 1]:
                best = max(best, dp[a - 1, b - 1] + sim[a - 1, b - 1])
            dp[a, b] = best
    out, a, b = [], n, m
    while a > 0 and b > 0:
        s = sim[a - 1, b - 1]
        if ok[a - 1, b - 1] and abs(dp[a, b] - (dp[a - 1, b - 1] + s)) < 1e-9:
            out.append((oi[a - 1], nj[b - 1], float(s)))
            a, b = a - 1, b - 1
        elif dp[a - 1, b] >= dp[a, b - 1]:
            a -= 1
        else:
            b -= 1
    return out[::-1]


def align(
    old: list[DocUnit], new: list[DocUnit],
    ncfg: NormalizationConfig = DEFAULT, ac: AlignConfig | None = None,
    embed: Callable[[list[str]], np.ndarray] | None = None,
) -> tuple[list[Pair], list[str]]:
    """Returns (pairs, notes). Unmatched indices are deletions/additions."""
    ac = ac or AlignConfig()
    notes: list[str] = []
    pairs: list[Pair] = []

    idf = term_weights(old, new) if ac.distinctive_terms else None

    # ---- Layer 1: exact anchors (in order)
    anchors = _exact_anchors(old, new, ncfg)
    for i, j in anchors:
        pairs.append(Pair(i, j, "exact", 1.0))
    used_o = {p.i for p in pairs}
    used_n = {p.j for p in pairs}

    # ---- Layer 1b: identical text at a different position (relocation, exact), first match by order
    by_norm: dict[str, list[int]] = {}
    for j in range(len(new)):
        if j not in used_n:
            by_norm.setdefault(new[j].norm, []).append(j)
    for i in range(len(old)):
        if i in used_o or not old[i].norm:
            continue
        cands = by_norm.get(old[i].norm)
        if cands:
            j = cands.pop(0)
            pairs.append(Pair(i, j, "relocated_exact", 1.0, review=len(cands) > 0, relocated=True))
            used_o.add(i); used_n.add(j)

    # ---- Layer 2: fuzzy DP inside the gaps between anchors (keeps document order)
    bounds = [(-1, -1)] + anchors + [(len(old), len(new))]
    for (a0, b0), (a1, b1) in zip(bounds, bounds[1:]):
        oi = [i for i in range(a0 + 1, a1) if i not in used_o]
        nj = [j for j in range(b0 + 1, b1) if j not in used_n]
        matched = _dp_align(old, new, oi, nj, ac, idf)
        free_o = sorted(set(oi) - {i for i, _, _ in matched})
        free_n = sorted(set(nj) - {j for _, j, _ in matched})
        # bound the rival search to ~max_dp_cells comparisons per gap (nearest free units by position)
        k = max(1, ac.max_dp_cells // (4 * max(1, len(matched))))
        for i, j, s in matched:
            _check(ac)
            raw = lexical_similarity(old[i], new[j], idf)
            # Ambiguity: an UNPAIRED unit in the same gap is (almost) as similar as the chosen partner, i.e. the
            # pairing was decided by the section/kind bonus or by order, not by the text => a human should decide.
            rival = max([lexical_similarity(old[i], new[x], idf) for x in _near(free_n, j, k)] +
                        [lexical_similarity(old[x], new[j], idf) for x in _near(free_o, i, k)], default=0.0)
            ambiguous = rival >= ac.fuzzy_review and rival >= raw - ac.ambiguity_margin
            frame = not ambiguous and s >= ac.fuzzy_modified and phrase_swap(old[i], new[j])
            pairs.append(Pair(i, j, "fuzzy", round(raw, 4), review=s < ac.fuzzy_modified or ambiguous or frame,
                              ambiguous=ambiguous, frame_only=frame))
            used_o.add(i)
            used_n.add(j)

    # ---- Layer 3 (pass 2): relocation search over leftovers, any position
    left_o = [i for i in range(len(old)) if i not in used_o]
    left_n = [j for j in range(len(new)) if j not in used_n]
    if left_o and left_n:
        cand: list[tuple[float, int, int]] = []
        if len(left_o) * len(left_n) > ac.max_dp_cells:  # too many leftovers for an all-pairs fuzzy search
            notes.append(f"Very large unmatched remainder ({len(left_o)} x {len(left_n)} passages): the out-of-order relocation "
                         "search was limited to identical text; moved-and-edited passages appear as REMOVED + ADDED.")
            norm_n: dict[str, list[int]] = {}
            for j in left_n:
                norm_n.setdefault(new[j].norm, []).append(j)
            cand = [(1.0, i, j) for i in left_o for j in norm_n.get(old[i].norm, [])]
        else:
            for i in left_o:
                _check(ac)
                for j in left_n:
                    s = similarity(old[i], new[j], ac.section_bonus * 0.5, idf, ac.bonus_share_floor)
                    if old[i].norm == new[j].norm:
                        s = 1.0
                    if s >= ac.relocation_min:
                        cand.append((s, i, j))
        cand.sort(key=lambda t: (-t[0], t[1], t[2]))
        by_i: dict[int, list] = {}
        by_j: dict[int, list] = {}
        for c in cand:
            by_i.setdefault(c[1], []).append(c)
            by_j.setdefault(c[2], []).append(c)
        for s, i, j in cand:
            if i in used_o or j in used_n:
                continue
            rivals = [c for c in by_i[i] + by_j[j] if (c[1] == i and c[2] != j and c[2] not in used_n) or (c[2] == j and c[1] != i and c[1] not in used_o)]
            ambiguous = any(s - c[0] <= ac.ambiguity_margin for c in rivals)
            exact = old[i].norm == new[j].norm
            raw = 1.0 if exact else round(lexical_similarity(old[i], new[j], idf), 4)
            pairs.append(Pair(i, j, "relocated_exact" if exact else "relocated_fuzzy", raw, review=ambiguous, relocated=True))
            used_o.add(i)
            used_n.add(j)

    # ---- Layer 4: semantic rewording candidates (never auto-confirmed)
    left_o = [i for i in range(len(old)) if i not in used_o]
    left_n = [j for j in range(len(new)) if j not in used_n]
    if embed is not None and left_o and left_n and len(left_o) * len(left_n) > 4 * ac.max_dp_cells:
        notes.append("Semantic matching skipped: too many unmatched passages for the similarity matrix.")
    elif embed is not None and left_o and left_n:
        try:
            eo = embed([old[i].body for i in left_o])
            en = embed([new[j].body for j in left_n])
            S = eo @ en.T
            order = sorted(((float(S[a, b]), int(a), int(b)) for a, b in np.argwhere(S >= ac.semantic_min)), reverse=True)
            for s, a, b in order:
                i, j = left_o[a], left_n[b]
                if i in used_o or j in used_n:
                    continue
                pairs.append(Pair(i, j, "semantic", float(s), review=True))
                used_o.add(i)
                used_n.add(j)
        except Exception as exc:  # embeddings are optional: degrade, but say so
            notes.append(f"Semantic matching skipped: {type(exc).__name__}")
    elif embed is None:
        notes.append("Semantic (embedding) matching was not run; reworded provisions without lexical overlap appear as REMOVED + ADDED.")
    pairs.sort(key=lambda p: (p.j, p.i))
    return pairs, notes
