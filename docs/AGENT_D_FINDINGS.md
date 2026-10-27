# Agent D: security / reliability / documentation audit

Environment: Python 3.12.3, FastAPI 0.159, Starlette 1.9, python-multipart 0.0.32, PyMuPDF 2.0, isolated temp `DATA_DIR`. Reproductions were scripted against the real app (TestClient and a real `uvicorn` process for the final upload/host checks). Scope: local single-user tool; nothing here is a claim of production security.

## Findings

| ID | Severity | Reproduced? | Evidence (before fix) | Status |
|---|---|---|---|---|
| D1 | High | Yes | 6 parallel uploads of identical bytes: statuses `[201,201,500,500,500,500]`; `IntegrityError` on `documents.sha256`; 4 orphaned `<uuid>.pdf` files left in `uploads/` | Fixed (`pipeline.ingest_pdf` catches the unique violation, reuses the winner's record, removes its file). Test: `test_concurrent_identical_uploads_*` |
| D2 | High | Yes | Default `RATE_LIMIT_PER_MINUTE=60` applied to every route incl. GET. Simulated Workspace polling (1 req/s) hit 429 at request #61; Dashboard polling (2 req/1.5 s = 80/min) is over the limit permanently. Normal UI use breaks | Fixed: separate read bucket (1200/min) vs write bucket (60/min), lock-protected. Tests: `test_polling_does_not_hit_the_default_limit`, `test_write_bucket_is_strict_and_separate` |
| D3 | Medium | Yes | `MAX_UPLOAD_MB=1`, 5 MB upload: 413 returned, but `SpooledTemporaryFile.rollover` fired (body fully written to system temp by the multipart parser before the handler ran). Limit was only enforced after the whole body arrived | Fixed: ASGI middleware checks Content-Length and counts received bytes (also chunked, verified with curl `Transfer-Encoding: chunked` against real uvicorn: 413). Tests: `test_oversize_*` |
| D4 | Medium | Yes | Comparison left `running` while process died: after re-running `create_app()` it stayed `running` forever (UI polls indefinitely, retry refused with 409 `not_failed`) | Fixed: `pipeline.recover_interrupted()` at startup marks them `failed/interrupted` (retryable). Single-process assumption. Test: `test_interrupted_comparisons_*` |
| D5 | Medium | Yes | Exception inside `extract_pdf`/ingest: HTTP 500 and the moved `<uuid>.pdf` stayed on disk (no record) | Fixed (cleanup on any exception). Test: `test_failed_ingest_leaves_no_orphan_file` |
| D6 | Medium | By source + test | Starlette's `ServerErrorMiddleware` calls the 500 handler then **re-raises**, so uvicorn still prints the full traceback; SECURITY.md claim "only exception types are logged" was false for unexpected route errors (SQLAlchemy errors can embed bound parameters, i.e. document text) | Fixed: `SecurityMiddleware` converts to generic 500, logs type only, does not re-raise. Test: `test_unhandled_exception_is_generic_*` |
| D7 | Medium | Yes | `_set()` on a deleted comparison raised `AttributeError` on `None`; the error handler then called `_set` again and the exception escaped the background task (deleting a comparison/document during a run) | Fixed: `Gone` handling, job abandons quietly. Test: `test_job_for_deleted_comparison_does_not_raise` |
| D8 | Medium | By analysis + test | No Host or Origin validation: a web page you visit can (a) `POST` multipart uploads to `http://localhost:8000/api/documents/upload` (a "simple" cross-site request, executed even though CORS hides the response) and (b) with DNS rebinding read every document via the unauthenticated API | Fixed (browser-side mitigations only): Host allow-list (400) and Origin check on POST/DELETE (403). Tests: `test_dns_rebinding_*`, `test_cross_origin_post_*`. Not a substitute for authentication |
| D9 | Medium | Yes | `reporting.to_html` emits `old_section`/`new_section` unescaped. Change row with section `<script>alert(1)</script>` appears verbatim in the HTML report. Section text derives from document headings, so a crafted PDF can inject script. Mitigated by `Content-Security-Policy: sandbox` + `attachment` disposition (scripts do not run when opened from the download), but not by escaping | **Open: reporting.py is Agent B's.** Fix: `e(_loc(...))` at the three `_loc` call sites in `to_html` (change table old/new cells and card header). Test present as `xfail` (`test_html_report_escapes_section_labels`); other fields (text, inline diff spans, labels, names, AI fields) verified escaped |
| D10 | Low | Yes | Docker: `0.0.0.0` bind + `ports: 8000:8000` / `8080:80` publish an unauthenticated API on all host interfaces | Open (docker-compose.yml / Dockerfile not in my ownership). Recommend `127.0.0.1:8000:8000`, `127.0.0.1:8080:80`. Documented in SECURITY.md |
| D11 | Low | Yes | Files were unlinked before the transaction committed (delete flows), so a failed commit lost files while rows remained | Fixed: `unlink_after_commit` (dropped on rollback). Test: `test_files_survive_a_rolled_back_delete` |
| D12 | Low | Yes | Empty upload returned `not_a_pdf` 415 (the `empty_file` branch was unreachable) | Fixed (422 `empty_file`) |
| D13 | Low | Yes | `_id()` accepted non-ASCII "alphanumerics" (e.g. Arabic-Indic digits) so they reached the DB (returned 404 not 422); filenames kept control chars and Windows backslash paths | Fixed (ASCII only; names sanitised) |
| D14 | Low | Source | Half-written report file left when export generation raised; stale `tmp-*` upload files after a crash | Fixed (cleanup in export; startup tmp sweep > 1 h) |
| D15 | Low | Yes | Re-uploading identical bytes with a different label silently overwrites the stored label | Open by design; documented |
| D16 | Info | Source | PDF bombs: `fitz.open` and the page loop run in-process with no time/memory cap; page cap checked after open; a decompression-bomb PDF within 25 MB could exhaust memory. **Not reproduced** (no heavy test run) | Open; extraction code is Agent C's. Would need a subprocess/rlimit |
| D17 | Info | Source | Comparison timeout is checked only between stages (not inside `compare_units`/embedding); a stuck stage is not killed | Open; stated honestly in docs |
| D18 | Info | Source | Markdown report is unescaped (document text can inject Markdown/HTML into a `.md` file); `|` in file names breaks tables | Open, low priority, Agent B |
| D19 | Info | Checked | Prompt-injection controls in `rag.py`/`local_llm.py` match SECURITY.md (fence, `[E#]` validation, no tools). The question text is not fenced (it is the user's own input). Excerpt header attributes (`source=...`) use internal values only | No change needed |
| D20 | Info | Checked | `docs/API.md` still says "All routes are rate limited (`RATE_LIMIT_PER_MINUTE`)"; now reads use `RATE_LIMIT_READ_PER_MINUTE` | Open (not my file): report to lead |

Verified OK (no issue found): magic-byte check, `../`, null-byte, 400-char unicode and `C:\` file names never reach the filesystem (UUID names; display name sanitised); content-type spoofing ignored; temp file removed on 413/415/422 paths; report download resolves only DB-recorded names, is `attachment` + CSP sandbox + nosniff; document delete cascades to pages/units/chunks/comparisons/changes/reports and removes files (test `test_delete_document_removes_everything`); retention purge removes only expired documents (test); CORS is an explicit origin list, no credentials; frontend has no `dangerouslySetInnerHTML`; frontend error handler reads only `detail.code/message`.

## Files changed

- `backend/app/main.py`: `SecurityMiddleware` (Host/Origin checks, body cap, generic 500, headers), startup `recover_interrupted` + `clean_stale_tmp`.
- `backend/app/core/limits.py` (two buckets + lock), `core/config.py` (`rate_limit_read_per_minute`, `allowed_hosts`, docker UI origins `:8080` in `cors_origins`), `core/db.py` (`unlink_after_commit`).
- `backend/app/api/routes.py` (ids ASCII, filename sanitising, empty-file code, deferred file deletes, export cleanup), `backend/app/services/pipeline.py` (ingest race/cleanup, `Gone`, `recover_interrupted`).
- `backend/tests/test_security.py` (new, 22 tests + 1 xfail); `backend/tests/test_api.py::test_rate_limit` adjusted one line (GETs now use the read bucket).
- `docs/SECURITY.md` rewritten; this file.

## Licence table (from installed package metadata in `backend/.venv`; not legal advice)

| Package | Version | Declared licence | Implication |
|---|---|---|---|
| pymupdf | 2.0 | "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License" | AGPL-3.0 copyleft (network-use clause) unless you buy a commercial licence. Fine for an AGPL-compatible open-source project; closed or SaaS use needs a commercial licence or a different extractor. Confirm with Artifex/counsel |
| pymupdf-layout, pymupdf4llm | 2.0 | **No licence declared in metadata** (installed as dependencies of pymupdf 2.0; not imported by DocuDiff, not in `requirements.txt`) | Unknown from metadata; check upstream before redistributing an environment containing them |
| sentence-transformers | 6.2.0 | Apache-2.0 | Permissive. Model `all-MiniLM-L6-v2` is Apache-2.0 per its card (not checked offline) |
| torch | 2.14.1 | Apache-2.0 AND BSD-2/3 (and LLVM exception) | Permissive |
| transformers | 5.20.2 | Apache-2.0 | Permissive |
| fastapi / starlette / uvicorn | 0.159.0 / 1.9.0 / 0.54.0 | MIT / BSD-3 / BSD-3 | Permissive |
| pydantic, pydantic-settings | 2.14.1 / 2.16.1 | MIT | Permissive |
| sqlalchemy | 2.1.6 | MIT | Permissive |
| rapidfuzz | 3.14.6 | MIT | Permissive |
| reportlab | 5.0.1 | BSD licence | Permissive |
| numpy / httpx / python-multipart | 2.5.4 / 0.28.1 / 0.0.32 | BSD-3 (+ others) / BSD-3 / Apache-2.0 | Permissive |
| pillow | 13.0.0 | MIT-CMU | Permissive |
| Ollama models (e.g. Llama 3.2) | n/a | Each model's own licence | Not checked; see model card |

Frontend (npm) licences were not audited. Vulnerability scanning: `pip-audit` is not installed, so **no dependency vulnerability scan was performed**.

## Test results

Baseline before changes: 49 passed. After:

```
$ cd backend && python -m pytest -q
......................................................................x  [100%]
70 passed, 1 xfailed in 5.85s
```

(70 = 49 original + 21 new passing; the xfail is D9, owned by Agent B. Once `reporting.to_html` escapes sections, remove the `xfail` mark.)

Final live check with `uvicorn` and `MAX_UPLOAD_MB=1`: Content-Length 3 MB upload 413; chunked 3 MB upload 413; `Host: evil.com` 400; `/api/health` 200; no files left in `uploads/`.

## Requests to other owners

- Agent B (`reporting.py`): escape `_loc(...)` output in `to_html` (D9); optionally escape Markdown (D18).
- Lead: docker-compose/Dockerfile localhost-only port publishing (D10); `docs/API.md` rate-limit sentence (D20). Docker UI relies on `ALLOWED_HOSTS` containing `backend` and `CORS_ORIGINS` containing `http://localhost:8080` (both now defaults); not tested with real Docker.
- Agent C (`pdf_extraction`): consider an extraction time/memory guard or subprocess (D16).

## Limitations of this audit

No Docker run, no fuzzing, no PDF-bomb test, no frontend/npm audit, no vulnerability database scan, no penetration test. Findings reflect the code and behaviour observed on one macOS machine.
