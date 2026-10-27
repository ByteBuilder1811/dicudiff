# Agent A: comparison algorithm and evaluation audit

Scope: `backend/app/services/{section_alignment,diff_engine}.py`, `backend/scripts/benchmark.py`, evaluation methodology.
All numbers below come from commands run on 2026-10-22 (Apple Silicon, Python 3.12.3 venv). Per-pair timings were taken
while other agents were using the same machine, so they are noisy. Every result is **synthetic, from clean
template-generated PDFs**. None of it shows production readiness.

## 1. Why added/removed recall is 0.983 with hashing and 0.95 with MiniLM

Reproduce with the code as it was before any change:

```bash
cd backend && . .venv/bin/activate
python scripts/benchmark.py --split test --embedding hashing --show-misses
python scripts/benchmark.py --split test --embedding sentence-transformers --show-misses   # ALLOW_MODEL_DOWNLOAD unset
```

Both runs reproduced the README numbers exactly: hashing 59/60 added and 59/60 removed, MiniLM 57/60 and 57/60.
Precision was 1.00 and modified recall was 1.00 in both runs. Every miss is listed below:

| seed | backend | layer / method | score | old record | new record |
|---|---|---|---|---|---|
| 1004 | both | Layer 2 fuzzy DP → NEEDS_REVIEW (low confidence) | raw lexical 0.539 (ranking 0.629 with +0.08 section bonus, both numbered 3.4) | 3.4 The reporting entity shall verify the identity of each applicant and keep a copy for 22 years. | 3.4 The bank shall report suspicious activity concerning linked accounts and keep a copy for 69 years. |
| 1007 | MiniLM only | Layer 4 semantic → NEEDS_REVIEW | cosine 0.937 (lexical 0.854, below the 0.88 relocation floor) | 1.2 The service provider shall encrypt stored copies of identity documents within 60 days of onboarding. | 2.4 The institution shall encrypt stored copies of identity documents within 62 days of onboarding. |
| 1016 | MiniLM only | Layer 4 semantic → NEEDS_REVIEW | cosine 0.865 (lexical 0.714) | 3.3 The branch manager shall authenticate remote requests from corporate users at least once every 26 months. | 1.3 The branch manager shall authenticate remote requests from corporate users and keep a copy for 63 years. |

**How it happens.** The hashing embedder sets `semantic = False`, so `pipeline.run_comparison` passes `embed=None`
and Layer 4 never runs. With MiniLM, Layer 4 runs over the clauses that are still unmatched after Layers 1 to 3.
In seeds 1007 and 1016, those leftovers are a gold-REMOVED clause and a gold-ADDED clause that the generator made
almost identical: same action and object, and in 1016 the same subject too. The cosine is ≥ 0.78, so Layer 4 pairs
them and the pair goes to NEEDS_REVIEW. The strict metric only counts a gold ADDED item as found when a record of
category `ADDED` carries it. A NEEDS_REVIEW record therefore turns one gold ADDED and one gold REMOVED into two
false negatives, and creates no false positive.

**Which of (a), (b) and (c) it is.**
- **(b) The metric and the ground truth are the main cause.** (i) The strict metric scores NEEDS_REVIEW as a miss
  by definition. (ii) The generator's de-duplication (`clause()`) only rejects exact duplicates, or clauses that
  share the text before `" within"`. Near-duplicate clauses are therefore common. In the test split, 38 of 60 gold
  added clauses share action and object with another clause in the same document, and 9 of 60 share subject, action
  and object. On dev it is 11 of 20 and 3 of 20. For seed 1007, the gold label "removed + added" is itself debatable:
  a human could reasonably read it as "provision moved from §1.2 to §2.4, with the subject and the number of days
  changed".
- **(c) The semantic layer behaves as designed.** It never auto-confirms, and it flags a plausible rewording for
  review. With the counting below, surfaced recall is **1.00 for both backends**. No gold change is silently lost.
- **(a) There is a smaller algorithm weakness, behind the miss shared by both backends (seed 1004).** Two unrelated
  clauses that share a template frame ("... and keep a copy for N years") and the same clause number score 0.54 on
  lexical similarity. The +0.08 section bonus then pushes them over the 0.50 review floor. Section 4 below covers the
  change made for this. The case is routed to review. It is never reported as a confident MODIFIED.

The 0.983 vs 0.95 gap is a difference of 2 items out of 60. The 95% Wilson intervals are 59/60 [0.911, 0.997] and
57/60 [0.863, 0.983]. They overlap, so the gap is not statistically meaningful on its own. The mechanism above
explains it completely.

## 2. Audit of the evaluation method

| Issue | Finding | Action |
|---|---|---|
| NEEDS_REVIEW scoring | Under the strict metric, NEEDS_REVIEW is a false negative and never a false positive. Recall therefore drops whenever the system honestly asks a human. | `benchmark.py` now reports **strict** and **surfaced** recall separately and never merges them. It also reports the number of review records, classified against gold (`true_modified_pair` / `removed_x_added_pair` / `other`). Recommendation: publish strict P/R/F1, surfaced recall, and review workload (count) together. Count NEEDS_REVIEW as "surfaced for a human", not as an error and not as a success. |
| Set semantics | The old script built Python **sets** of found texts, so a duplicated ADDED record was collapsed and never counted as a false positive. | Changed to multiset (`Counter`) scoring. The test fixture checks that a duplicate record is a false positive. |
| Arithmetic | The old numbers were recomputed independently with `docs/benchmarks/recompute_metrics.py`. It is stdlib-only, has its own normalisation and its own loops, and shares no code with the benchmark. | The recompute agrees exactly on test/hashing, test/MiniLM, fresh/hashing and fresh/MiniLM. `tests/test_eval_metrics.py` checks both implementations against hand-computed numbers and re-scores the committed fresh dumps. |
| How hard the modifications are | `modify()` only makes one-token edits: number +2..9, shall→may, shall→shall not. They score ≥ 0.93 lexically, so "modified 1.00" is easy. There is no rewording, split, merge, relocation or renumbering (`renumber_shift` is always 0, and `truth["moved"]` is always empty). | Documented here. MODIFIED = 1.00 should not be read as handling real rewording. The unit tests in section 4 cover the missing cases. |
| Near-duplicate generator | See section 1. Gold labels for near-duplicate removed+added pairs are ambiguous. | Generator left unchanged so that past splits stay reproducible. Documented in a comment in `clause()`. |
| Gold matching | Gold is matched by normalised body text after stripping the clause number. Clauses are unique strings by construction, so this is sound here. It would not be sound for documents with duplicate clauses. | No change needed. Note: only the text is checked, not the page. |
| "Alignment accuracy" | It only checks that each unchanged clause appears in some UNCHANGED record. Headings were excluded. | Now requires old body == new body, and reports `headings_unchanged_accuracy` separately (1.00 on all splits). |
| Dense "hashing" retrieval | `HashingEmbedder` uses hashed word n-grams. It is lexical, not semantic, so its "dense" and "hybrid" retrieval numbers say nothing about semantic retrieval. | Already labelled "(lexical)" in the README. Keep that label. |
| Retrieval query leakage | The query is `"what must be done to " + <verb phrase copied verbatim from the gold clause>`, which strongly favours BM25. Recall@1 of about 0.8 is limited by the same action phrase appearing in several clauses: 20 actions across about 16 clauses per document. It is not limited by model quality. | Documented with a comment in the script. Retrieval numbers should not be presented as evidence of retrieval quality. Paraphrased queries would be needed for that (owner: retrieval agent). |
| Small n | 30 pairs gives 60 gold items per category per split. One miss costs 1.7 points of recall. | Report counts (tp/fp/fn), not just ratios. These are now in the JSON. |
| Split hygiene | `test` (1000–1029) had already been looked at during the earlier lexical-similarity fix. | It is relabelled **previously evaluated**. A new **fresh** split (5000–5029, `--split fresh`) was run **once**, after all tuning was finished. Dev (0–9) was the only split used for decisions. |

## 3. Results

Commands, all run from `backend/` with the venv active. MiniLM runs use `--embedding sentence-transformers` with
`ALLOW_MODEL_DOWNLOAD` unset.

```bash
python scripts/benchmark.py --split dev   --embedding {hashing|sentence-transformers}
python scripts/benchmark.py --split test  --embedding {hashing|sentence-transformers} --show-misses
python scripts/benchmark.py --split fresh --embedding hashing --show-misses \
    --dump ../docs/benchmarks/fresh_hashing_records.json --out ../docs/benchmarks/fresh_hashing.json
python scripts/benchmark.py --split fresh --embedding sentence-transformers --show-misses \
    --dump ../docs/benchmarks/fresh_minilm_records.json --out ../docs/benchmarks/fresh_minilm.json
python3 -I ../docs/benchmarks/recompute_metrics.py ../docs/benchmarks/fresh_hashing_records.json ../docs/benchmarks/fresh_hashing.json
```

Added and removed results were identical in every run, so the table shows one figure for both. Format: strict
P / R / F1 · surfaced recall · NEEDS_REVIEW count.

| Split (seeds) | Status | Code | Backend | Added = Removed | Modified P/R | Unchanged-clause alignment | NEEDS_REVIEW (all removed×added) |
|---|---|---|---|---|---|---|---|
| dev (0–9) | tuning split | before | hashing | 1.00 / 0.95 / 0.974 · 1.00 | 1.00 / 1.00 | 1.00 | 1 (seed 8, raw 0.636) |
| dev (0–9) | tuning split | after | hashing | 1.00 / 0.95 / 0.974 · 1.00 | 1.00 / 1.00 | 1.00 | 1 (seed 8, raw 0.523) |
| dev (0–9) | tuning split | after | MiniLM | 1.00 / 0.95 / 0.974 · 1.00 | 1.00 / 1.00 | 1.00 | 1 |
| test (1000–1029) | **previously evaluated** | before | hashing | 1.00 / 0.983 / 0.992 · 1.00 | 1.00 / 1.00 | 1.00 | 1 (seed 1004, raw 0.539) |
| test (1000–1029) | **previously evaluated** | before | MiniLM | 1.00 / 0.95 / 0.974 · 1.00 | 1.00 / 1.00 | 1.00 | 3 (1004, 1007, 1016) |
| test (1000–1029) | **previously evaluated** | after | hashing | 1.00 / 0.983 / 0.992 · 1.00 | 1.00 / 1.00 | 1.00 | 1 (seed 1004, raw 0.43) |
| test (1000–1029) | **previously evaluated** | after | MiniLM | 1.00 / 0.95 / 0.974 · 1.00 | 1.00 / 1.00 | 1.00 | 3 (same seeds) |
| **fresh (5000–5029)** | **untouched, run once after tuning** | after | hashing | 1.00 / 0.967 / 0.983 · 1.00 | 1.00 / 1.00 | 1.00 (358 clauses) | 2 (5021 raw 0.558, 5025 raw 0.452) |
| **fresh (5000–5029)** | **untouched, run once after tuning** | after | MiniLM | 1.00 / 0.967 / 0.983 · 1.00 | 1.00 / 1.00 | 1.00 | 2 (same) |

For reference, the pre-change code was also run on fresh. This was evaluation only, with no tuning afterwards. It
gave identical categories: hashing and MiniLM both 0.967 strict, 1.00 surfaced, and the same 2 review pairs with raw
scores 0.595 and 0.505.

Retrieval on fresh, hybrid: hashing R@1 0.765, R@3 1.00, MRR 0.878. MiniLM R@1 0.787, R@3 1.00, MRR 0.888. Citation
page correctness 1.00. End-to-end time, measured under machine contention: hashing 0.039 s/pair, MiniLM 0.224 s/pair.

Summary: the fresh split confirms the same pattern as before. Precision is 1.00, modified recall is 1.00, and nothing
is silently lost. Strict recall is reduced only by template-sharing clauses that occupy the same numbered slot and
are routed to review. On fresh, MiniLM did not add or remove any review item compared with hashing. The algorithm
changes **did not change any category on any benchmark split**. They lowered the reported confidence of the spurious
review pairs, and they fix the unit-level cases in section 4. JSON files: `docs/benchmarks/fresh_*.json`,
`fresh_*_records.json`, `fresh_*_misses.txt`, `{dev,test}_{hashing,minilm}_after_agentA.json`.
`test_hashing.json` and `test_minilm.json` are the earlier results and are now labelled "previously evaluated".

## 4. Code changes (files)

- `backend/app/services/section_alignment.py`
  1. **Distinctive-term weighting** (`term_weights`, `AlignConfig.distinctive_terms=True`). The word-Dice part of
     `lexical_similarity` is weighted by `log(1 + N/df)`, with df counted **per version** (max over old and new). A
     first version computed df over both documents pooled. It penalised genuine correspondences, because every
     shared word has df ≥ 2, and it broke `test_api.py::test_review_workflow`. Per-version df fixed that. The change
     was justified on dev only: across all non-corresponding clause pairs in dev, pairs scoring ≥ 0.50 dropped from
     460 to 309 out of 2352, and p99 dropped from 0.741 to 0.677. The minimum true one-token modification went from
     0.952 to 0.936.
  2. **Ambiguity check in Layer 2.** A DP pairing becomes NEEDS_REVIEW (`Pair.ambiguous`) when an unpaired unit in
     the same gap is within `ambiguity_margin` (0.03) of the chosen partner's raw similarity. Before the fix, the
     section-number bonus silently picked one candidate as a confident MODIFIED.
  3. Tried and **reverted**: making the review floor depend on raw similarity, so that the section bonus could not
     create a pairing. It broke `test_api.py::test_review_workflow`, which expects a heavy rewording in the same
     numbered slot to be offered for review. In-place replacement and in-place rewording cannot be told apart by
     position alone, so review is the honest outcome.
- `backend/app/services/diff_engine.py`: added an explanation and the flag `ambiguous_match` for ambiguous pairings.
  Before, the label read "Low-confidence match (similarity 0.92)", which was misleading. Semantic NEEDS_REVIEW
  records now also carry `critical_flags`, so a number or negation change inside a semantic candidate is not hidden.
- `backend/scripts/benchmark.py`: `--split fresh`, importable (main is guarded), pure `score_pair` with multiset
  semantics, strict and surfaced metrics, F1, tp/fp/fn counts, review breakdown, heading accuracy, `--show-misses`,
  `--dump`. The generator is unchanged, byte-for-byte, so earlier splits stay reproducible.
- New: `docs/benchmarks/recompute_metrics.py`, `backend/tests/test_eval_metrics.py`, this file, and the result JSONs.

## 5. New tests (`backend/tests/test_diff_engine.py`, `backend/tests/test_eval_metrics.py`)

Against the original algorithm, `test_modified_clause_with_equally_similar_new_sibling_goes_to_review` and
`test_semantic_candidate_keeps_high_risk_flags` **failed**. They pass after the changes. The remaining tests pass on
both versions and pin behaviour, including known limitations, which they label explicitly.

- Unrelated clauses sharing template vocabulary are never a confident MODIFIED. The distinctive-term weights
  down-weight template words, and a one-word edit stays ≥ 0.75.
- A small rewording gives MODIFIED (≥ 0.75). A large rewording never gives MODIFIED. With embeddings it can only
  give NEEDS_REVIEW.
- A **split** clause becomes MODIFIED + ADDED, and a **merge** becomes MODIFIED + REMOVED. These are known
  limitations, but nothing is silently lost.
- Repeated boilerplate removed from section 2 is reported REMOVED with the page and index from section 2.
- A section swap with renumbering gives 3 POSSIBLY_RELOCATED, plus renumbered UNCHANGED, and no ADDED, REMOVED or
  MODIFIED.
- A clause added or deleted next to a similar sibling gives the correct single ADDED or REMOVED. A modified clause
  with an equally similar new sibling gives NEEDS_REVIEW (`ambiguous_match`) + ADDED.
- High-risk tokens, parametrised over date, amount scale (million→billion), decimal %, legal reference, KYC-7→9,
  shall→may, must→should, "not" inserted, and "unless" removed: MODIFIED with the correct flag.
- `xfail(strict)`: **alphanumeric identifier ABC123→ABC124 raises no high-risk flag.** The cause is in
  `normalization.py`, which another agent owns.
- Footer noise ("Page 1 of 4" vs "Page 1 of 5") and renamed headings are never a confident MODIFIED. A page-break
  fragment that extraction failed to merge gives MODIFIED + REMOVED (known limitation).
- Evidence check over a document that produces UNCHANGED, MODIFIED, POSSIBLY_RELOCATED and NEEDS_REVIEW (with a fake
  embedder): every record has category, method and explanation. Every non-ADDED record has old text and page, and
  every non-REMOVED record has new text and page. Numbered clauses have a section_id. Paired records have a
  confidence in [0, 1]. Review records are `pending` and have an inline diff.
- `test_eval_metrics.py`: hand-computed P/R and surfaced numbers. The independent recompute matches `score_pair`.
  NEEDS_REVIEW is never a false positive. The committed fresh dumps re-score to the stored JSON.

## 6. Remaining failures and limitations, and what other owners need to do

1. Clauses that share a template frame and **the same clause number** still reach NEEDS_REVIEW, through the +0.08
   section bonus, even with raw similarity around 0.43–0.56. This is the only cause of strict-recall loss on all
   three splits. It is a deliberate trade-off: removing the bonus from eligibility would drop genuine heavy
   rewordings in the same slot, which `test_review_workflow` covers. Possible next step, not done here to avoid
   tuning blind: make the bonus scale with distinctive-term overlap.
2. A long shared frame with a different subject matter is still a confident **MODIFIED** at about 0.80. Example:
   "submit a report on cyber security incidents ... quarter" vs "... unclaimed deposits ... year". The character
   ratio dominates. No regression test is pinned for this. It is a known limitation.
3. No split or merge detection (1→N, N→1).
4. ADDED and REMOVED explanations say "closest candidates scored below the match thresholds" but give no score.
   Adding the best candidate's score would make that evidence-backed.
5. **For the normalization owner:** identifiers with digits glued to letters (ABC123) are not critical tokens. See
   the xfail test.
6. **For the extraction owner:** footer and page-number lines that leak into units show up as NEEDS_REVIEW noise, and
   paragraphs continued across a page break that are not merged show up as MODIFIED + REMOVED.
7. **For the README and docs owner:** please replace the README's "Measured results" table with the fresh-split
   numbers, label 1000–1029 as "previously evaluated", add surfaced recall and NEEDS_REVIEW count, and say that
   benchmark modifications are one-token edits only. The README's "49 tests" count is out of date (see below).
8. **For the retrieval owner:** benchmark queries copy the gold clause's verb phrase. Paraphrased queries are needed
   before claiming anything about dense or semantic retrieval.

## 7. Final test run

See the final pytest output appended below.
