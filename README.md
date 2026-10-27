# DocuDiff — local, zero-cost document comparison & RAG

Upload two versions of a PDF (circular, contract, policy, spec). DocuDiff aligns every paragraph, classifies each change as
`ADDED · REMOVED · MODIFIED · UNCHANGED · POSSIBLY_RELOCATED · NEEDS_REVIEW`, and cites **page + clause** for each one.
Deterministic findings and AI interpretation are kept visibly separate. Everything runs on your machine: no paid API, no
account, no document ever leaves localhost. **The app is fully useful without any LLM.**

| Layer | Choice |
|---|---|
| Backend | Python 3.11–3.13, FastAPI, SQLAlchemy (SQLite default) |
| PDF | PyMuPDF (+ optional Tesseract OCR) |
| Compare | difflib LCS anchors, RapidFuzz, order-preserving DP alignment, optional MiniLM embeddings |
| RAG | own BM25, dense vectors (SentenceTransformers), Reciprocal Rank Fusion, optional cross-encoder rerank |
| LLM | Ollama (local), strict "evidence is untrusted data" prompting, citation validation |
| Frontend | React + TypeScript + Vite + Tailwind |

## Quick start (macOS / Linux)

```bash
# 1. backend
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -r requirements-ml.txt          # optional: local semantic embeddings (PyTorch, a few hundred MB)
python scripts/download_models.py           # optional: one-off ~90 MB embedding model download
python scripts/make_samples.py              # synthetic demo PDFs -> data/samples
uvicorn app.main:app --port 8000            # API docs: http://localhost:8000/docs

# 2. frontend (second terminal)
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Optional local LLM (explanations + Q&A prose). DocuDiff **never** downloads a model by itself:

```bash
ollama pull llama3.2:3b     # ≈2 GB disk, ≈4 GB RAM. Set OLLAMA_MODEL to any installed model.
```

Optional OCR for scanned PDFs: `brew install tesseract` (or `apt install tesseract-ocr`) and `OCR_ENABLED=true`.
Docker: `docker compose up --build` (UI on :8080; Ollama stays on the host).
Config: copy `.env.example` → `.env`. Without Tesseract, scanned PDFs are *detected and rejected with a clear message*, never treated as empty documents.

## Demo workflow (≈2 minutes)

1. `python backend/scripts/demo.py` (with the backend running) uploads the sample KYC circular v1→v2 and a second, independent pair, and prints the findings; or do the same in the UI via **New comparison** using `data/samples/kyc_circular_v1.pdf` / `kyc_circular_v2.pdf`.
2. In the workspace: filter by category, search, tick **High-risk only** (numbers/dates/%/negations/shall→may), watch word-level highlights.
3. Ask *"Which clauses were removed?"* / *"Did the minimum threshold change?"* — answers cite `[E#]` sources you can click and inspect.
4. **Export** HTML / Markdown / PDF.
5. **Common removals** → pick `kyc_circular v1→v2` and `northwind v3→v4`: the "video based identification" clause is found removed in both pairs, with evidence from each.

## Verify

```bash
cd backend && source .venv/bin/activate
python -m pytest -q                                           # 189 tests (engine, extraction, retrieval/RAG, API, reporting, security)
python scripts/benchmark.py --split test --embedding sentence-transformers
```

## How comparison works (summary — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md))

1. **Extract** units (heading/paragraph/list/table) with page numbers; page-break continuations are merged; repeated headers/footers removed.
2. **Normalize** whitespace/Unicode/line-break hyphenation; numbers, dates, %, negations and legal references are never altered.
3. **Align**: exact in-order anchors (LCS) → exact relocation → order-preserving fuzzy DP between anchors → global relocation search (second pass) → optional semantic candidates that can only be `NEEDS_REVIEW`.
4. **Classify** with an explanation, method, confidence, and risk flags (`number_changed`, `negation_changed`, `obligation_strength_changed`, …).
5. **Review**: ambiguous pairs are queued; a human confirms ("same provision") or rejects (→ REMOVED + ADDED).

## Measured results (synthetic data — read the caveats)

Benchmark: generated PDF pairs with **known** changes (2 modified, 2 removed, 2 added clauses per pair; different page breaks per version).
Reproduce: `cd backend && python scripts/benchmark.py --split fresh --embedding sentence-transformers` (also `--embedding hashing`, `--split dev|test`).
Raw JSON in [docs/benchmarks](docs/benchmarks); an independent re-implementation of the metrics (`docs/benchmarks/recompute_metrics.py`) agrees with the script.

| Split (30 pairs unless noted) | Status | Added / Removed precision | Strict recall (surfaced recall incl. review) | Modified P / R | NEEDS_REVIEW items |
|---|---|---|---|---|---|
| fresh, seeds 5000–5029 | untouched until the final run | 1.00 | 0.967 (1.00) — same with and without embeddings | 1.00 / 1.00 | 2 |
| test, seeds 1000–1029 | **previously evaluated** (not untouched) | 1.00 | 0.983 hashing / 0.95 MiniLM (1.00 both) | 1.00 / 1.00 | 1 / 3 |

* **"Strict recall"** counts a clause only if labelled ADDED/REMOVED. Clauses the engine routes to `NEEDS_REVIEW` (a human decides) count as misses under it; "surfaced recall" counts them as caught. The 0.983→0.95 gap seen earlier is 2 clauses of 60 and is explained by the embedding layer pairing near-duplicate removed/added clauses for review (details: [docs/benchmarks/AGENT_A_FINDINGS.md](docs/benchmarks/AGENT_A_FINDINGS.md)). Intervals overlap; it is not a significant difference.
* Fresh split: unchanged-clause alignment 1.00 (358 clauses); hybrid retrieval Recall@1 0.77–0.79, Recall@3 1.00, MRR 0.88–0.89; citation page correctness 1.00; 0.04 s/pair (lexical) or 0.18 s/pair (MiniLM) end to end on an Apple-silicon laptop.
* **Caveats:** clean, template-generated PDFs; modifications are one-token edits (number, shall→may, "not"); the retrieval queries copy the gold clause's verb phrase, so retrieval numbers say little about real semantic retrieval; the dense "hashing" backend is lexical. These numbers are **not evidence of production readiness** or of accuracy on real documents.
* LLM (measured on this 8 GB Mac with `llama3.1:8b` via Ollama): first call timed out at >170 s while the model loaded; a warm cited answer took ≈81 s. `llama3.2:3b` was **not tested**. Use ≤3B models on 8 GB machines.

## Licenses (verify before redistributing)

FastAPI, SQLAlchemy, RapidFuzz, reportlab, React, Vite, Tailwind: permissive (MIT/BSD). SentenceTransformers & `all-MiniLM-L6-v2`: Apache-2.0.
**PyMuPDF is AGPL-3.0** (commercial licence available) — fine for an open-source portfolio project under AGPL-compatible terms; for closed-source use swap the extractor (e.g. `pypdf`, BSD) behind `pdf_extraction.extract_pdf`.
Ollama models carry their own licences (e.g. Llama 3.x community licence) — check the model card.

## Limitations & future work

- Splits/merges of paragraphs between versions may show as MODIFIED/REMOVED/ADDED rather than one change.
- Tables, footnotes, multi-column layouts are best-effort; OCR quality depends on Tesseract.
- Comparison timeout is cooperative (checked between stages); a single stuck stage cannot be killed.
- Rate limiting is in-process and per-IP; no authentication (single-user local tool).
- Future: split/merge detection, table-aware diff, PostgreSQL + pgvector store (interface in `retrieval.py`), streaming LLM answers, side-by-side PDF page rendering, DOCX input.

More: [Portfolio report](PORTFOLIO_REPORT.md) · [Architecture](docs/ARCHITECTURE.md) · [API](docs/API.md) · [Security & threat model](docs/SECURITY.md) · [Browser verification](docs/BROWSER_VERIFICATION.md) · [Demo script](docs/DEMO_SCRIPT.md)
