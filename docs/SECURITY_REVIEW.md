# Independent security review — status and findings

**Honest status: the independent second review was NOT completed.** The reviewer started on 2026-10-22, but repeatedly ran unbounded stress tests in the foreground and had to be stopped by the lead after ~30 minutes, before writing its report. No independent verdict on Agent D's fixes, prompt-injection handling, or `SECURITY.md` claims exists. What follows is limited to what was actually observed or measured.

## What the reviewer got to before being stopped (code changes, verified by the lead)

| Area | Change | Verified |
|---|---|---|
| Alignment resource exhaustion | `align()` now takes a cooperative deadline (`AlignConfig.deadline`, raises `AlignTimeout`, checked inside the O(n·m) loops); the greedy fallback for large gaps is windowed so it never builds the n×m matrix (`max_dp_cells` 4M → 1M); `lexical_similarity` no longer scans the vocabulary on every call; `pipeline.run_comparison` passes the deadline and handles `AlignTimeout` | full suite passes (189 tests); timings below |
| Review of its other planned checks (Host/Origin bypasses, report XSS with hostile labels, prompt-injection fuzzing, logging) | **not done** by this reviewer | — |

## Measurements by the lead (hard 60 s cap per case, synthetic units, worst case = nothing matches exactly so one giant gap)

| Units per version | Alignment time |
|---|---|
| 200 | 1.6 s |
| 500 | 10.0 s |
| 1,000 | 42.4 s |
| 2,000 | 18.2 s (windowed fallback engages above 1M cells; fewer pairings are examined) |

Interpretation: worst-case alignment cost grows roughly quadratically until the fallback engages, then is bounded; with the cooperative deadline the comparison fails with a clear timeout instead of running unbounded. The fallback gives up optimal pairing in huge gaps, so quality on very large, almost-entirely-changed documents is lower than on the benchmark. Not tested on real large documents.

Extraction with many text blocks: synthetic single-page PDFs with 500–8,000 tiny text items extracted in ≤0.2 s (MuPDF merges tiny items into few blocks), so an extraction blow-up could **not** be reproduced. As a precaution `MAX_BLOCKS_PER_PAGE = 3000` now truncates a page with a warning, and the list-membership test in the two-column detection (an O(n²) pattern) was replaced by an identity set. A regression test covers the truncation warning.

## Claims by Agents C and D — verification level

* Verified by lead re-running the suite (188 passing, twice; 189 after the final fix, no flakiness): behaviour covered by `tests/test_security.py`, `test_rag.py`, `test_llm_fallback.py`, `test_extraction_edge.py`, `test_reporting.py`.
* **Not independently verified** (taken from the agents' own reports): concurrent-upload race fix against a real server, Content-Length/chunked oversize handling against real uvicorn, Host/Origin check bypass resistance, DNS-rebinding protection, absence of document text in logs on all error paths.
* Not performed by anyone: dependency vulnerability scan (`pip-audit` not installed), Docker run, fuzzing, memory-cap/subprocess isolation for PDF parsing.

## Scope reminder

Local, single-user tool. No authentication, no TLS, no tenant isolation. Do not expose the port to a network. This document is not a production-security claim.
