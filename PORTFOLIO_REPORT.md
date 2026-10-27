# DocuDiff — Portfolio Report

*Everything here was measured or observed in this repository on one Apple-silicon Mac (8 GB RAM) on 2026-10-22. Where something was not tested, it says so.*

## 1. Purpose and architecture

DocuDiff compares two versions of a PDF (circulars, contracts, policies) and reports every change with page and clause evidence, entirely on the local machine — no paid API, no account, no document leaves localhost. The deterministic engine is separated from optional AI: extraction, alignment, classification, highlighting, retrieval-based Q&A and report export all work without an LLM.

Stack: Python 3.12 · FastAPI · SQLAlchemy/SQLite · PyMuPDF · RapidFuzz · own BM25 · SentenceTransformers (MiniLM, optional) · Ollama (optional) · React + TypeScript + Vite + Tailwind. Architecture diagram: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Comparison pipeline: extract units → normalise (meaning-preserving) → exact LCS anchors → exact relocation → order-preserving fuzzy DP between anchors (similarity = char ratio × distinctive-term word overlap) → global relocation pass → optional semantic candidates (always `NEEDS_REVIEW`, never auto-confirmed) → classify into ADDED / REMOVED / MODIFIED / UNCHANGED / POSSIBLY_RELOCATED / NEEDS_REVIEW with explanation, method, confidence and risk flags (number, date, %, amount, identifier, legal reference, negation, shall/may).

## 2. Features implemented and how each was verified

| Feature | Verification |
|---|---|
| PDF upload/extraction (headings, paragraphs, page continuation, header/footer removal, scanned-PDF detection) | automated tests incl. blank, corrupt, encrypted, scanned, mixed, 400-page, two-column PDFs |
| Six-category comparison with evidence, review queue (confirm/reject) | automated tests + browser |
| Word-level highlights, high-risk flags, filters, search, pagination | browser |
| Source-cited Q&A, insufficient-evidence refusal, extractive fallback | automated tests (fake local LLM) + browser (extractive mode) |
| Optional Ollama explanations/answers with validated `[E#]` citations | tests against a fake Ollama server; one real call earlier (see §6) |
| Common-removals across two pairs | automated tests + browser |
| Export to HTML / Markdown / PDF | automated tests + downloaded and opened via the UI |
| Retention deletion, upload validation, rate limiting, host/origin checks | automated tests; some checked against a real uvicorn |

## 3. Test results

* Baseline (before this audit): **49 passed**, frontend build OK.
* Final (lead re-run, twice): **189 passed**, 0 failed, 0 flaky across runs; `npx tsc -b && npx vite build` passes.
* Gaps: no frontend unit/e2e test tooling (manual browser checklist in [docs/BROWSER_VERIFICATION.md](docs/BROWSER_VERIFICATION.md)); no Docker run; no `pip-audit`.

## 4. Evaluation methodology and independently recalculated metrics

Synthetic generator → PDFs with known gold changes → full pipeline → metrics. Metrics were recomputed by a separate stdlib-only script (`docs/benchmarks/recompute_metrics.py`, agrees exactly) and counted as multisets (duplicate records are false positives). Dev seeds 0–9 informed decisions; seeds 1000–1029 had already been looked at, so they are labelled **previously evaluated**; seeds 5000–5029 (**fresh**) were run once after tuning.

| Split | Backend | Added/Removed P | strict R | surfaced R | Modified P/R | NEEDS_REVIEW |
|---|---|---|---|---|---|---|
| fresh 5000–5029 | lexical (no embeddings) | 1.00 | 0.967 (58/60) | 1.00 | 1.00/1.00 | 2 |
| fresh 5000–5029 | MiniLM | 1.00 | 0.967 (58/60) | 1.00 | 1.00/1.00 | 2 |
| test 1000–1029 (previously evaluated) | lexical | 1.00 | 0.983 (59/60) | 1.00 | 1.00/1.00 | 1 |
| test 1000–1029 (previously evaluated) | MiniLM | 1.00 | 0.95 (57/60) | 1.00 | 1.00/1.00 | 3 |

Fresh split also: unchanged-clause alignment 1.00 (358 clauses), hybrid retrieval Recall@3 1.00, MRR 0.88–0.89, citation page correctness 1.00, 0.04 s (lexical) / 0.18 s (MiniLM) per pair.

**Embedding discrepancy — explained, not a bug:** the 0.983 vs 0.95 gap is 2 clauses of 60 (95% Wilson intervals overlap). With embeddings, an almost-identical removed and added clause are paired and sent to `NEEDS_REVIEW`; the strict metric counts review items as misses. Counting items surfaced for a human, recall is 1.00 for both. A contributing cause is the generator producing many near-duplicate clauses (38/60 added clauses share action and object with another clause), making some gold labels debatable. Full analysis: [docs/benchmarks/AGENT_A_FINDINGS.md](docs/benchmarks/AGENT_A_FINDINGS.md).

## 5. Dataset limitations

Clean template PDFs; 30 pairs (60 gold items per category — one miss moves recall 1.7 points); modifications are one-token edits only; no real rewording, split/merge, relocation or renumbering in the benchmark (these are covered by unit tests, not benchmark data); retrieval queries copy the gold clause's verb phrase. **Do not read these numbers as real-world accuracy.**

## 6. Local-model measurements

* `llama3.1:8b` via Ollama on this 8 GB machine: first request timed out at >170 s (model load + CPU spill); a warm cited answer took ≈81 s. Output was correct and cited valid sources.
* `llama3.2:3b`: **not installed, not tested** (would need ≈2 GB disk; not pulled without approval).
* MiniLM embeddings: model load 44.7 s cold / 8.8 s warm; ≈12–27 ms per question on the sample pair (11 chunks); embedding 1,415 chunks took 18.8 s. Extraction of a 400-page, 936k-character synthetic PDF: 2.7 s, ≈190 MB RSS.
* Without Ollama the app falls back to extractive, cited answers; Ollama calls have a 3 s connect timeout and a configurable total timeout.

## 7. Browser workflows verified

Upload (via the real input/drop handlers — the OS file dialog itself was not exercised), workspace filtering/search/pagination, review confirm/reject, citation click-through, extractive Q&A and insufficient-evidence answer, common-removals (sample pairs → 1 confirmed removal with evidence from both pairs; empty/guard states), HTML/MD/PDF export downloaded and the PDF opened, backend-offline state, mobile 375 px. Not verified: Safari/Firefox, the enabled "Explain with AI" state (no model installed), keyboard-opening the file chooser; the failed-comparison state was simulated in the database (the Retry click itself was real). Details and screenshots: [docs/BROWSER_VERIFICATION.md](docs/BROWSER_VERIFICATION.md).

## 8. Security scope

Scope is a **local, single-user tool**: no authentication, no TLS, no tenant isolation, not hardened for network exposure. Audit fixes (each reproduced before fixing and covered by tests): concurrent-duplicate-upload race (500s + orphan files), rate limiter breaking normal UI polling, oversize uploads spooled to disk before rejection, comparisons stuck "running" after a crash, cross-origin/DNS-rebinding access to the local API (Host/Origin checks), traceback leakage, HTML report injection via section labels, citation validation, prompt-fence escaping, PDF resource caps. An independent second review was started but **not completed** (it was stopped after repeated unbounded stress tests); what it did finish — a deadline and bounded fallback for large-document alignment — plus the lead's own measurements are in [docs/SECURITY_REVIEW.md](docs/SECURITY_REVIEW.md). Worst-case alignment (nothing matching) measured 1.6 s / 10 s / 42 s at 200 / 500 / 1,000 units per version, with a bounded fallback beyond that. Open: no hard memory cap/subprocess isolation for PDF parsing; in-process rate limiting; cooperative (not preemptive) comparison timeout; no dependency vulnerability scan (`pip-audit` unavailable). Details: [docs/SECURITY.md](docs/SECURITY.md), [docs/AGENT_D_FINDINGS.md](docs/AGENT_D_FINDINGS.md).

Licensing: PyMuPDF is dual-licensed AGPL-3.0 / Artifex commercial — fine for an open-source release under AGPL-compatible terms; swap the extractor for closed-source use. Not legal advice.

## 9. OCR and database limitations

OCR is **unverified**: Tesseract is not installed here; only scanned-page detection and the "OCR enabled but Tesseract missing" path were tested. Database is SQLite only; there is no PostgreSQL/pgvector store and no Alembic migrations (schema via `create_all`) — the retrieval code keeps embeddings as blobs behind a function boundary but no alternative backend exists or was tested.

## 10. Known issues / future work

Split and merge clauses show as MODIFIED+ADDED / MODIFIED+REMOVED; a long shared template frame with different subject matter can still be a confident MODIFIED (~0.80 similarity); same-slot template clauses go to review (the only cause of strict-recall loss); tables detected only when cells share a line; `10,00,000` vs `1000000` don't match in BM25; ₹ renders as a box in PDF reports; no hard memory cap on PDF parsing; paraphrased-query retrieval evaluation; real-document evaluation set; test `llama3.2:3b`.

## 11. Setup and reproduction

```bash
cd "new cv projects"/docudiff        # wherever you cloned it
make setup                           # needs Python 3.11–3.13; creates backend/.venv, installs frontend deps
python3 backend/scripts/make_samples.py   # (after `. backend/.venv/bin/activate`) regenerates sample PDFs
make dev-backend                     # terminal 1 → http://localhost:8000/docs
make dev-frontend                    # terminal 2 → http://localhost:5173
make test                            # 189 tests
cd backend && . .venv/bin/activate && python scripts/benchmark.py --split fresh --embedding sentence-transformers
python scripts/download_models.py    # optional one-off ~90 MB embedding model (ALLOW_MODEL_DOWNLOAD=true)
ollama pull llama3.2:3b              # optional local LLM (~2 GB)
```

## CV bullets (all verified above; adjust wording, do not inflate)

* Built DocuDiff, a fully local PDF version-comparison tool (FastAPI, React/TypeScript, SQLite, PyMuPDF): layered paragraph alignment with six evidence-backed change categories, risk flags for changed numbers/dates/negations, a human-review queue, and HTML/Markdown/PDF reports — no paid APIs; optional local embeddings and Ollama.
* Added local hybrid RAG (own BM25 + MiniLM dense + rank fusion) with citation validation, insufficient-evidence refusal and an extractive fallback when no LLM is available; verified with 189 automated tests plus manual browser walkthroughs.
* Evaluated on 30 untouched synthetic document pairs (precision 1.00, strict recall 0.97, all missed items surfaced for human review), independently recomputed the metrics, and diagnosed an apparent embedding-related recall drop as a metric artefact; ran a security audit that found and fixed an upload race, rate-limit/UI conflict, and cross-origin exposure of the local API.
