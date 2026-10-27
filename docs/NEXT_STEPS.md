# Paused work — where things stand (read this first)

Work was paused mid-way at the user's request. Two agents were stopped while editing, so the tree contains **unverified changes**. Nothing below has been claimed as verified unless stated.

## Last fully verified state
189 backend tests passed, frontend build OK, fresh-split benchmark reproduced and independently recomputed (see `PORTFOLIO_REPORT.md`, `docs/benchmarks/final_fresh_*.json`). That was **before** the changes listed below.

## Changes made after that point (UNVERIFIED unless noted)

| Area | Files | Status |
|---|---|---|
| OCR | `tests/test_ocr.py` (new); Tesseract 5.5.3 installed via Homebrew | **Verified**: OCR on a scanned PDF works; 2 tests pass (skip cleanly if Tesseract is missing) |
| Local LLM | `ollama pull llama3.2:3b` done; `qwen2.5-coder:7b` deleted (user-approved) to free disk | **Measured** (under CPU contention from other agents): cited Q&A 73–90 s, explain 27 s, ≈2.2 GB RAM; unrelated question correctly refused; the 3B model gave a **wrong, uncited** answer to "Which clauses were removed?" |
| Uncited-answer guard | `app/services/rag.py`: an LLM answer with no valid citation is no longer shown as the answer; deterministic evidence is shown, model text kept as `llm_answer_unverified` | Code change made; **2 tests in `tests/test_rag.py` still assert the old behaviour and fail** (`test_invalid_or_missing_citations_are_flagged[...[E77][E88]...]`, `test_model_output_ids_are_validated_even_if_injection_wins`). My first test edit applied; the second was not saved. Fix: assert `mode == "extractive"`, `answer != llm_answer_unverified`, and that the stripped model text is in `llm_answer_unverified` (it is stored with unknown ids removed, and the extractive answer legitimately quotes document text such as the injected sentence). |
| Alignment (split/merge, template-frame, same-slot bonus) | `section_alignment.py`, `diff_engine.py`, `tests/test_diff_engine.py` | **Agent stopped mid-work; its changes are untested together.** It reported dev benchmark unchanged and was about to run the full suite and the single fresh-split run. |
| Hardening (isolated PDF parsing, numeric tokenizer, ₹ glyph, footer/page-break fixes, optional API token, Docker) | `pdf_extraction.py`, `retrieval.py`, `reporting.py`, `Dockerfile`, `docker-compose.yml`, `docs/SECURITY.md`, `tests/test_hardening.py` | **Agent stopped mid-work (was building the Docker frontend image).** `pip-audit` / `npm audit` results were not collected. |

## To resume
1. `cd backend && . .venv/bin/activate && python -m pytest -q` — expect the 2 known `test_rag.py` failures plus possibly more from the stopped agents' edits; fix or revert per file (the pre-change versions are not in git; `docs/` and the previous results describe expected behaviour).
2. If the alignment edits pass all tests, re-run the benchmarks (`--split dev`, then `--split fresh` once) and update `PORTFOLIO_REPORT.md` / `README.md` numbers; if not, revert `section_alignment.py`/`diff_engine.py` changes and keep the 189-test behaviour.
3. Re-run `npx tsc -b && npx vite build`.
4. Run `pip-audit` (install into the venv) and `npm audit --omit=dev`; try `docker compose build` (needs ≈3 GB free).
5. Update README/PORTFOLIO test counts and the limitations list to match what actually got verified.

## Limitations that remain regardless
No authentication/TLS (local single-user tool), SQLite only (no PostgreSQL/pgvector/Alembic — a scope choice), clean-PDF synthetic benchmark only, `llama3.2:3b` quality is limited (see above), real-document accuracy untested.
