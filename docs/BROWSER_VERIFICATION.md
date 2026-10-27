# Browser verification (Agent B)

Environment: built-in browser pane (Chromium, 1095x722 desktop viewport; 375x812 for mobile), Vite dev server on 127.0.0.1:5173,
backend `EMBEDDING_BACKEND=hashing` on 127.0.0.1:8000 with an isolated throw-away `DATA_DIR`, Ollama reachable but `llama3.2:3b` NOT installed
(so the UI is in "retrieval only" mode). Both pairs of sample PDFs in `data/samples` were used, plus synthetic PDFs (needs-review pair, 122-change
list, long unbroken tokens). Everything below was run in this session unless marked otherwise.

Honest limits of the tooling: the browser tools have no native file-chooser, so PDFs were handed to the real `<input type=file>` / drop zone by
JavaScript (`DataTransfer` + `change` / `drop` events) after being fetched from a local CORS helper. The React handlers, upload request, extraction
and navigation are the real ones; the OS file dialog itself was not exercised. The browser pane was reported as "hidden" while I worked, so
animations (smooth scrolling) were throttled; where that mattered it is noted.

## Checklist

| # | Workflow | Method | Result | Notes |
|---|---|---|---|---|
| 1a | Upload via file input (slot A) | JS-injected File into `input[type=file]`, `change` event | PASS | doc card shows name, pages, "text extracted"; native OS dialog not exercised |
| 1b | Upload via drag/drop (slot B) | synthetic `DragEvent` drop with DataTransfer | PASS | |
| 1c | Same file in both slots | UI | PASS | error "Both slots contain the same file", Compare disabled; dedup notice shown |
| 1d | Compare -> navigates to workspace | real click | PASS | |
| 2a | Category filter chips (`aria-pressed`) | real + JS clicks | PASS | |
| 2b | Deselect ALL categories | JS | FAIL then fixed | showed all 17 changes incl. UNCHANGED (API treats empty filter as "all"); fixed in Workspace.tsx |
| 2c | Search | JS | PASS | "video" -> 1 result; nonsense -> "No changes match" empty state |
| 2d | High-risk toggle | JS | PASS | 11 -> 6 |
| 2e | Show-more pagination | JS on 61-change comparison | PASS | 50 -> 61, label "(11 remaining)" |
| 2f | Review confirm (NEEDS_REVIEW) | real click | PASS | becomes MODIFIED, buttons disappear, chip counts update |
| 2g | Review reject | real click | PASS | becomes REMOVED and a new ADDED row appears |
| 2h | Citations/page refs | visual | PASS | every side shows `Version · p.N · §section` |
| 2i | Explain-with-AI button, LLM model missing | visual/JS | PASS | disabled with tooltip; header shows "llm: llama3.2:3b" with grey dot. Enabled state NOT VERIFIED (no model installed; no paid API used) |
| 2j | Ask: suggested question (extractive) | real click | PASS | "Extractive answer - no language model used", [E1]/[E2] citations |
| 2k | Ask: click [E#] -> source highlighted & visible | real click | FAIL then fixed | evidence `<details>` stayed collapsed so the highlighted card was invisible; also ids `src-E1` were duplicated across answers so a later answer's [E1] targeted the first answer's card |
| 2l | "Show in changes" | JS click | PASS (desktop) | change card centred in view. Smooth-scroll on mobile tab NOT VERIFIED visually (pane hidden); tab switch verified |
| 2m | Ask unrelated question | JS | PASS | "insufficient evidence", amber box, evidence list auto-opened |
| 3a | Common removals kyc v1->v2 x northwind v3->v4 | form_input + real click | PASS | 1 confirmed, "exact 1.00", A evidence p.1 §2.3, B evidence p.1 §1.2, both panels shown |
| 3b | Direction text | visual | PARTIAL | backend text uses labels ("Version A -> Version B ... AND Version A -> Version B") which is ambiguous with default labels. Frontend now adds a "Pair A: file -> file - Pair B: ..." line. Backend wording (`services/common_removals.py`) left to lead |
| 3c | Empty result | UI | PASS | "No provision was removed in both pairs / Pair A removed 2 and pair B removed 0" |
| 3d | Same pair in A and B | UI | PASS | button disabled |
| 3e | Fewer than two completed comparisons | fresh empty DATA_DIR | PASS | "This workflow needs two completed comparison pairs" |
| 3f | Error state | not reproducible with valid input | NOT-VERIFIED for common-removals specifically; the shared ErrorBox path was verified for backend-offline on other pages |
| 3g | Select dropdown | find/form_input | FAIL then fixed | `Select` component was declared inside the page component, so it remounted on every state change (lost focus/open state; stale element refs). Also identical file-name pairs were indistinguishable; options now carry date + id |
| 4a | PDF button -> file downloads | real click | PASS | `docudiff-report-9b83a5e5.pdf` landed in ~/Downloads, opens in PyMuPDF (1 page) |
| 4b | PDF/HTML/MD downloads | real click | PASS (HTML, MD, PDF each seen on disk) | repeating a download with an identical file name did not overwrite in the pane (browser behaviour) |
| 4c | Popup blocking | code review + behaviour | FIXED | `window.open` ran after an awaited request (outside user activation; popup-blocked in Safari/Firefox, blank tab flash in Chromium). Replaced by a same-tab `<a download>` click. Safari/Firefox themselves NOT VERIFIED |
| 4d | PDF contents vs comparison | pymupdf text extraction + rendered page image | PASS after fixes | summary, documents table, change table, details, unresolved, limitations present; counts match UI (6/3/2) |
| 4e | PDF long text / unbroken tokens / escaping / non-Latin | unit tests | FAIL then fixed | see bugs |
| 5a | Backend offline | stopped uvicorn | FAIL then fixed | showed "Request failed (502)" (Vite proxy); now "Cannot reach the DocuDiff backend. Is it running on port 8000?" and workspace error has Retry |
| 5b | Unknown comparison id | UI | PASS | "Comparison not found." + Retry |
| 5c | Failed comparison + Retry | **simulated**: row set to `failed` in SQLite, then real Retry click | PASS | error box with message and Retry; Retry completes the comparison. A genuine engine failure was not reproducible with valid PDFs |
| 5d | Long result list | 122-change comparison | PASS | pagination fine; long unbroken token was clipped (fixed, see bugs) |
| 5e | Mobile 375px | resize_window | PASS with caveat | no horizontal page overflow; Changes/Ask tabs work; header nav scrolls horizontally (needs scroll to reach "Common removals" even after shrinking padding) |
| 5f | Keyboard | Tab key presses + focus inspection | PARTIAL | tab order logo -> nav -> slot labels -> drop zone; visible `:focus-visible` outline; Enter on textarea submits; drop zone handles Enter/Space. File chooser opened by keyboard NOT VERIFIED (cannot see OS dialog) |
| 7 | Screenshots | browser screenshot files copied | PASS | see below |

## Screenshots (docs/screenshots/, real captures)

`workspace-kyc-v1-v2.jpg`, `workspace-needs-review.jpg`, `ask-citation-highlight.jpg` (after fix), `common-removals-kyc-northwind.jpg`,
`common-removals-empty-state.jpg`, `failed-comparison-retry.jpg` (simulated failure), `mobile-workspace-375px.jpg`,
`dashboard-backend-offline-before-fix.jpg` (shows the pre-fix "Request failed (502)").
`pdf-export-before-fix-kyc-page1.png` (pre-fix PDF) and `pdf-export-downloaded-rev-pair-page1.png` are PyMuPDF renders of exported PDFs, not browser screenshots.
Browser screenshots are 800x527 JPEGs (the tool's resolution) except the mobile one. Note: the screenshot tool sometimes returns the frame from before the latest click.

## Bugs found and fixes

| Bug | Repro | Fix (files) | Test |
|---|---|---|---|
| HTML/PDF/MD reports did not escape `old_section`/`new_section` (heading text) -> `<script>` in HTML report; markup in PDF paragraphs | document with heading `<script>alert(1)</script>`, export HTML | `backend/app/services/reporting.py` (`html.escape`/XML-escape/Markdown-escape at every location, label, file name, text site; Markdown also neutralises raw `<`, pipes in table cells) | `tests/test_reporting.py` (escape + pipe tests), `tests/test_security.py::test_html_report_escapes_section_labels` (xfail mark removed; passes) |
| PDF export: non-Latin characters (₹, Devanagari, CJK) rendered as wrong glyphs/black boxes silently | unit repro | `reporting.py`: use a system Unicode TTF when the content needs one (Arial Unicode / DejaVu); otherwise `?` plus an explicit limitation line | `test_pdf_non_latin_text_is_not_silently_garbled` |
| PDF lacked Documents table and Change table; AI implications/caveats omitted; text silently cut at 900 chars | export PDF | `reporting.py` `to_pdf` rewritten (tables with wrapped cells, 1500 chars, explicit " [truncated]" marker in all formats) | `test_pdf_has_summary_change_table_detail_and_limitations`, `..._long_unbroken_text...` |
| PDF long unbroken tokens could overflow margins | 400-char token | `wordWrap="CJK"` in styles | `test_pdf_long_unbroken_text_stays_inside_margins...` |
| Deselecting all category chips shows every change | UI | `frontend/src/pages/Workspace.tsx` | manual (below) |
| Ask citation click did nothing visible (collapsed details) and `E1` ids collided between answers | ask 2 questions, click [E1] | `frontend/src/components/AskPanel.tsx` (turn-namespaced ids, controlled `<details>`) | manual |
| "Show in changes" no-op on mobile Ask tab | mobile viewport | `AskPanel.tsx` + `Workspace.tsx` (`showChange` switches tab first) | manual (tab switch verified; scroll not visible-verified) |
| Long unbroken text clipped inside change cards | search "Unbrokenword" in the 122-change pair (article scrollWidth 1127 > 653) | `ChangeRow.tsx`, `AskPanel.tsx`, `CommonRemovals.tsx` (`[overflow-wrap:anywhere]`, `min-w-0`) | manual, re-measured 653/653 |
| Common-removals `Select` remounted every render; duplicate-looking option names; ambiguous direction | UI | `frontend/src/pages/CommonRemovals.tsx` | manual |
| `window.open` after await for downloads | click PDF | `Workspace.tsx` (anchor download) | manual |
| Backend offline shows "Request failed (502)"; workspace load error has no Retry | stop backend | `frontend/src/services/api.ts`, `Workspace.tsx` | manual |
| Mobile: wordmark hidden below 480px, tighter header; tablist roles/`aria-selected` | | `Layout.tsx`, `Workspace.tsx` | manual |

No frontend test framework exists in the repo and I did not add one. Manual regression checklist for frontend fixes: (1) untick all chips -> empty state;
(2) ask two questions, click [E1] in the second -> its own evidence opens/highlights; (3) search a 120-char token -> wraps inside the card;
(4) stop backend -> dashboard/workspace say "Cannot reach the DocuDiff backend"; (5) click PDF -> file downloads, no blank tab.

## Tests run (final)

```
$ cd backend && . .venv/bin/activate && python -m pytest -q
........................................................................ [ 45%]
........................................................................ [ 90%]
...............                                                          [100%]
159 passed in 43.26s          (includes other agents' tests; baseline was 49)

$ python -m pytest tests/test_reporting.py tests/test_security.py::test_html_report_escapes_section_labels -q
........   8 passed in 4.39s

$ cd frontend && npx tsc -b && npx vite build
✓ 33 modules transformed.
dist/assets/index-DSJfvzt2.js   293.00 kB │ gzip: 91.99 kB
✓ built in 415ms
```

## Remaining issues / not verified

- Explain-with-AI enabled path and LLM-mode Ask were not exercised (no usable Ollama model; by design nothing was installed).
- Native file-chooser, Safari/Firefox popup/download behaviour, and visual smooth-scroll (pane hidden) are NOT VERIFIED.
- Real (non-simulated) failed comparison could not be provoked with valid PDFs.
- Common-removals direction string comes from the backend (`services/common_removals.py`) and is ambiguous with default labels; not my file.
- Each export click creates a new report file on the server (no reuse/cleanup) - backend concern.
- Header nav still needs horizontal scrolling on 375px screens.
- Bold is not available in the Unicode-font PDF path (Arial Unicode has no bold face); only used when content is non-Latin-1. The glyph for ₹ is absent in Arial Unicode (box) - install DejaVu/Noto for full coverage.
- Change-row `#N` uses global sequence numbers so hidden UNCHANGED rows leave gaps (#1, #3, ...); cosmetic, left as is.
- Servers started for this work (uvicorn, Vite, two CORS file helpers) were stopped; temp data lived under the session scratchpad.
