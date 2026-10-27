# API

Interactive schema: `GET /docs` (OpenAPI). Errors: `{"detail": {"code": "...", "message": "..."}}`. Ids are hex strings. All routes are rate limited (429): GET requests by `RATE_LIMIT_READ_PER_MINUTE`, all other methods by `RATE_LIMIT_PER_MINUTE`.

| Method & path | Purpose | Request → response |
|---|---|---|
| `GET /api/health` | status of LLM (reachable / model installed / hint), embeddings, limits | → `{status, llm, embeddings, ocr_enabled, limits}` |
| `GET /api/stats` | dashboard counters | → `{documents, comparisons, completed, running, failed, changes_by_category}` |
| `POST /api/documents/upload` | multipart `file` (.pdf, ≤`MAX_UPLOAD_MB`, `%PDF` magic checked), optional `label` | 201 → `{document, deduplicated}`; 413/415/422 |
| `GET /api/documents?limit&offset` | paginated list | → `{total,limit,offset,items}` |
| `GET /api/documents/{id}?include_text` | detail with per-page stats | → document + `pages[]`, `unit_count` |
| `DELETE /api/documents/{id}` | cascade delete (see ARCHITECTURE) | → `{deleted, comparisons_deleted}` |
| `POST /api/comparisons` | `{doc_a_id, doc_b_id, label_a, label_b, options{semantic, ignore_punctuation_case, renumbering_is_change}}` | 202 → comparison (poll it) |
| `GET /api/comparisons`, `GET /api/comparisons/{id}` | list / status (`queued·running·completed·failed`, `stage`, `progress`, `stats`, `limitations`, `error`) | |
| `POST /api/comparisons/{id}/retry` | re-run a failed comparison | 202 |
| `DELETE /api/comparisons/{id}` | delete comparison + reports | |
| `GET /api/comparisons/{id}/changes` | `category` (comma list), `q`, `risk_only`, `limit≤500`, `offset` | → `{items[], total, counts}`; each item: id, category, old/new text, page, section, match_method, confidence, explanation, flags, inline_diff, review_status, ai_explanation |
| `POST /api/comparisons/{id}/changes/{cid}/review` | `{decision: confirm\|reject}` for `review_status=pending` | → change (reject splits into REMOVED + ADDED) |
| `POST /api/comparisons/{id}/changes/{cid}/explain` | local-LLM explanation (labelled AI) | → change; 503 `llm_unavailable` with install hint |
| `POST /api/comparisons/{id}/questions` | `{question, k, use_llm, retrieval_mode: hybrid\|bm25\|dense, rerank}` | → `{answer, mode: llm\|extractive, sufficient_evidence, sources[], retrieval, llm_error, citation_warning}` |
| `POST /api/comparisons/{id}/export` | `{format: md\|html\|pdf, include_unchanged}` | → `{id, download_url}` |
| `GET /api/reports/{id}/download` | file download (CSP-sandboxed) | |
| `POST /api/common-removals` | `{comparison_a_id, comparison_b_id}` | → `{direction, items[{status, match_method, similarity, evidence_a, evidence_b}], confirmed, needs_review}` |
