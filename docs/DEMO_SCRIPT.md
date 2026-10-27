# Demo script (≈3 minutes, all local)

Prep: backend on :8000, `npm run dev` on :5173, sample PDFs from `data/samples` (`python backend/scripts/make_samples.py` regenerates them).

1. **Dashboard** — point out the header dots: embeddings / local LLM status. Say: "Nothing leaves this machine; the LLM is optional."
2. **New comparison** — drop `kyc_circular_v1.pdf` (older) and `kyc_circular_v2.pdf` (newer). Show extraction status and page counts. Compare.
3. **Workspace** — tick *High-risk only*: 30→15 days, Rs. 10,00,000→5,00,000, 2%→4%, five→ten years. Open one card: word-level highlight, page/clause on both sides, "why this classification".
4. **Removed / Added** — video-based identification removed, biometric clause added, new §5 grievance section.
5. **Ask** — "Which clauses were removed?" (deterministic evidence, `[E#]` citations; click one to jump to the source). Then an unrelated question → *insufficient evidence*.
6. **Export** — HTML, then PDF; open the PDF.
7. **Common removals** — compare `northwind_standard_v3.pdf` → `v4.pdf`, then open *Common removals*, choose both pairs: the video-identification clause is removed in both, with evidence from each pair.
8. (Optional, if Ollama + a small model is installed) **Explain with local AI** on a modified clause — shown in a separate, labelled "AI interpretation" box.

CLI alternative: `python backend/scripts/demo.py` prints the same findings.
