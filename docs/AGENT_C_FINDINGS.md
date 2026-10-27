# Agent C findings: local AI, retrieval, extraction

Scope: `retrieval.py`, `rag.py`, `local_llm.py`, `embeddings.py`, `pdf_extraction.py`, `synthetic.py` plus three new test files.
No real LLM call was made (llama3.1:8b and qwen2.5-coder:7b were not invoked; `llama3.2:3b`, the configured default, is not installed and is **untested**). LLM behaviour was tested against a fake local Ollama HTTP server. No model was pulled or downloaded, Tesseract is not installed.

## 1. Retrieval audit
- Embedding backend: `sentence-transformers` (all-MiniLM-L6-v2, cached locally) loads here. Measured load: 44.7 s first (cold import + disk), 8.8 s on a later process. Tests use the `hashing` backend (lexical, not semantic).
- BM25 is an own Lucene-IDF implementation; dense = cosine over float32 blobs; hybrid = RRF k=60. All three work (unit + integration tests).
- Chunk ids are content hashes (doc|idx|text): re-indexing is idempotent (test). Stale chunks are deleted and unchanged ones kept (test). A changed embedder `name` re-embeds all chunks and dense search ignores vectors from another model (test). Switching to "no embedder" keeps old vectors but search falls back to BM25 with a note.
- Per-request cost: `rag.answer` previously ran `index_document` (re-chunk + loads all chunks) for both docs on every question. It now calls `retrieval.ensure_indexed` (2 COUNT queries). Test confirms the only `embed()` call per question is the 1-text query. The embedder is a process singleton guarded by a lock (8-thread test: constructor runs once); a failed load is cached, not retried per request. Rerank CrossEncoder was reloaded on every request; now cached per process (only the "unavailable" path was exercised; no reranker model is configured or installed here).
- Measured latency, `rag.answer(use_llm=False)` on the sample KYC pair (11 chunks): hashing 2-6 ms, MiniLM 12-27 ms per question (includes query embedding). Synthetic 1415-chunk doc (hashing embedder): bm25 ~172 ms, dense ~83 ms, hybrid ~175 ms median per `search`; BM25 is rebuilt per query in pure Python, so ~1400+ chunks costs ~0.2 s. Acceptable, not optimised further (a per-document BM25 cache would be the next step). Embedding 1415 chunks with MiniLM took 18.8 s.
- Thread-safety: embedder cache is locked (reset now also locked); SentenceTransformer `encode` serialised by a lock. Reranker cache locked.

## 2. Citation correctness / insufficient evidence (tests/test_rag.py)
Covered: (a) `[E#]` -> version, doc id, page range, section; (b) invalid / missing / grouped citations (`[E1, E42]`, `[E77][E88]`) flagged and stripped; ids beyond the 10 excerpts shown to the model are rejected; (c) adversarial/unsupported questions return "insufficient evidence" without calling the LLM; (d) removed/added/changed questions return deterministic change records, stable across calls; (e) injection text appears only inside `<document_excerpt>`, cannot close/open blocks or break attributes, and model output ids are validated.

Bugs found and fixed:
1. **"what is the minimum airspeed" was judged sufficient** (raw 0.34 term coverage from the single word "minimum"; also "minimum age of a customer"). Now uses IDF-weighted coverage (>= 0.5; unseen terms weigh most), question words (many, much, when, why, can, ...) are stop words, and the dense criterion (>= 0.45) is applied only for a semantic embedder. Verified with both backends on 12 questions. Trade-off: hashing mode is stricter ("maximum penalty per month" becomes insufficient there; MiniLM keeps it sufficient). Thresholds are calibrated on tiny synthetic corpora only.
2. `retrieval_mode=dense` with no embedder returned arbitrary chunks (all scores 0). Now falls back to working BM25 with a note.
3. Prompt-attribute injection: section/version text went unescaped into `source="..."`. Now escaped.
4. Citation parsing missed `[E1, E2]`; unknown ids were accepted if they existed in sources but were not shown to the LLM. Fixed (`rag.validate_citations`).
5. Chunker: a single sentence > 900 chars (unpunctuated text) produced an unbounded chunk; sentence "overlap" could emit duplicate/oversized chunks. Fixed (`_hard_split`, overlap guard).
6. Chunks spanning pages were cited with `page_start` only; now cited as `pp.2–3`. Trailing "." stripped from section ids in citations (`§7.` -> `§7`).
7. "Which clauses were removed?" with zero removals now answers "no removed changes" deterministically instead of "insufficient evidence".

## 3. Ollama failure handling (tests/test_llm_fallback.py, fake server)
Each case yields a specific message, bounded time, and the extractive fallback in `rag.answer`: unreachable (fast fail), model not installed (hint with `ollama pull`, no chat call), chat timeout (1 s timeout -> error in < 4 s; connect timeout now 3 s), HTTP 404/500/503, malformed/empty body, non-JSON `/api/tags`. `status()` probe is capped at 2.5 s (test: 6 s-slow server returns in 2-5 s).
Fixed: `chat()` called `status()` on every call; added `cached_status()` (5 s TTL, cleared on any chat failure); `/health` still probes fresh. `explain_change` could raise (500) on invalid JSON inside braces or a JSON list; now never raises, returns a caveated result, coerces types and the string "false". HTTP errors previously all read "Local model call failed: HTTPStatusError". `/explain` returns 503 `llm_unavailable` with hint (tested for not-installed, 500, unreachable) and 200 with caveat for non-JSON output.
No hosted API: a source scan finds no hosted-provider references, and a runtime spy shows every httpx request in a RAG+LLM run goes to 127.0.0.1.

## 4. Extraction audit (tests/test_extraction_edge.py; generators added to synthetic.py)
Works: blank pages (-> `needs_ocr`), 0-page, empty/junk/garbage files (-> `failed`), encrypted (rejected; owner-password-only PDFs open), scanned (`needs_ocr`), mixed scanned+text (ready + partial warnings), two-column order (left then right), page-break merge incl. hyphenation (`docu-`/`mentation` -> "documentation" after normalization), very long single paragraph (8 pages, one unit; chunker now bounds it), repeated headers/footers removed, truncated PDF (PyMuPDF repairs; lost pages warned).
Bugs fixed: (1) with `OCR_ENABLED=true` and Tesseract missing, a page with < 20 chars of real text lost it, and the warning told the user to "enable OCR". Now text is kept and the warning says Tesseract is not installed; OCR is not retried per page. (2) Body font size was estimated before headers/footers were removed, turning body clauses into headings. (3) Pages containing a single block differing only in digits were all deleted as "running headers" (a 400-page test doc yielded zero units): headers now need >= 3 blocks on the page and never blank a page.
Resource guards (lead's request): page limit is checked before iterating (401-page test fails instantly); 400 pages x 936k chars extracted in 2.7 s, RSS ~190 MB; huge 14400x14400 pt MediaBox text page: 0.01 s. Added `MAX_TOTAL_CHARS` (5M) and `MAX_EXTRACT_SECONDS` (120 s, checked between pages) with "stopped before page N" warnings, and OCR dpi capped so pixmap sides <= ~4000 px. Not done: no hard memory cap or subprocess isolation; a single pathological page (or `fitz.open` on a crafted file) could still consume time/memory. Open limitation. Note the 5M-char cap truncation yields status `ready` with a warning, not a failure.
**OCR is UNVERIFIED**: only detection and the Tesseract-missing path are tested; OCR text quality was never exercised.
Limitations: tables are detected only when cells share a line as >= 3 spans; otherwise rows become paragraphs (cell text kept). Numeric formats like `10,00,000` vs `1000000` do not match in BM25.

## 5. Test results
`cd backend && . .venv/bin/activate && python -m pytest -q` -> `159 passed in 47.76s` (49 baseline + 81 mine + others' additions). My three files: 81 tests. One earlier full run showed `tests/test_api.py::test_review_workflow` failing (empty NEEDS_REVIEW list); it passed in later runs and does not touch my files (alignment area, flaky/in-flux: report to Agent A). Timing-based LLM tests have generous bounds because of machine load.

## Files changed
`app/services/{retrieval,rag,local_llm,embeddings,pdf_extraction,synthetic}.py`; new `tests/{test_rag,test_llm_fallback,test_extraction_edge}.py`; this doc.

## Requests for others
- Agent D: `/health` calls `local_llm.status()` (fresh probe, up to 2.5 s) every time; consider `cached_status()` if polled often. QuestionIn/response schema unchanged; sources now also carry `scores.weighted_coverage` and `page_end` (already present).
- README says 49 tests; now more.
