"""Safe PDF text extraction with PyMuPDF.

Never executes PDF content (no JavaScript/launch actions are interpreted by PyMuPDF's text extraction).
Output: pages, ordered structural units (heading/paragraph/list/table), metadata, warnings.
"""
import os
import pickle
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

from app.core.config import get_settings
from app.services.normalization import split_numbering
from app.services.types import DocUnit, ExtractionResult

MAX_TOTAL_CHARS = 5_000_000   # extraction stops (with a warning) beyond this many characters: bounds RAM/DB growth
MAX_EXTRACT_SECONDS = 120.0   # wall-clock budget for the page loop (checked between pages)
MAX_BLOCKS_PER_PAGE = 3000    # a page with more raw text blocks is truncated (with a warning): bounds sort/split cost
MAX_OCR_PIXELS_SIDE = 4000    # OCR pixmaps are rendered at a dpi capped so neither side exceeds this many pixels
MIN_CHARS_PER_PAGE = 20  # below this a page is considered image-only
_TERMINAL = re.compile(r"[.;:!?)\]\"”’]$")
_LIST_START = re.compile(r"^\s*(?:[-•●▪◦*]|\(?[0-9a-zA-Z]{1,3}[.)])\s+")


def _block_text(block: dict) -> tuple[str, float, bool]:
    """Join the lines of a PyMuPDF text block; return (text, max font size, is_bold)."""
    lines, sizes, bold_chars, chars = [], [], 0, 0
    for line in block.get("lines", []):
        parts = []
        for span in line.get("spans", []):
            t = span["text"]
            if not t.strip():
                parts.append(t)
                continue
            parts.append(t)
            sizes.append(span["size"])
            n = len(t.strip())
            chars += n
            if span["flags"] & 16 or "bold" in span["font"].lower():
                bold_chars += n
        lines.append("".join(parts).rstrip())
    return "\n".join(lines).strip(), (max(sizes) if sizes else 0.0), (chars > 0 and bold_chars / chars > 0.8)


def _looks_like_table(block: dict) -> bool:
    lines = block.get("lines", [])
    if len(lines) < 2:
        return False
    gappy = 0
    for line in lines:
        spans = [s for s in line.get("spans", []) if s["text"].strip()]
        if len(spans) >= 3:
            xs = [s["bbox"][0] for s in spans]
            if all(b - a > 20 for a, b in zip(xs, xs[1:])):
                gappy += 1
    return gappy >= max(2, len(lines) // 2)


def _line_style(line: dict) -> tuple[int, bool]:
    spans = [sp for sp in line.get("spans", []) if sp["text"].strip()]
    if not spans:
        return 0, False
    size = max(sp["size"] for sp in spans)
    bold = all(sp["flags"] & 16 or "bold" in sp["font"].lower() for sp in spans)
    return round(size), bold


def _split_block(block: dict) -> list[dict]:
    """MuPDF often merges consecutive lines into one block. Split where the font style changes (heading
    followed by body text) or where the vertical gap shows a paragraph break."""
    lines = [ln for ln in block.get("lines", []) if any(sp["text"].strip() for sp in ln.get("spans", []))]
    if len(lines) < 2:
        return [block]
    groups, cur = [], [lines[0]]
    for prev, ln in zip(lines, lines[1:]):
        size = max(_line_style(prev)[0], 1)
        gap = ln["bbox"][1] - prev["bbox"][3]
        if _line_style(ln) != _line_style(prev) or gap > 0.45 * size:
            groups.append(cur); cur = []
        cur.append(ln)
    groups.append(cur)
    if len(groups) == 1:
        return [block]
    return [{**block, "lines": g, "bbox": (block["bbox"][0], g[0]["bbox"][1], block["bbox"][2], g[-1]["bbox"][3])} for g in groups]


def _page_blocks(page: "fitz.Page") -> tuple[list[dict], bool]:
    """Returns (blocks in reading order, truncated)."""
    d = page.get_text("dict", flags=fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE)
    w = page.rect.width
    raw = [b for b in d["blocks"] if b["type"] == 0]
    truncated = len(raw) > MAX_BLOCKS_PER_PAGE
    blocks = [sb for b in raw[:MAX_BLOCKS_PER_PAGE] if _block_text(b)[0] for sb in _split_block(b)]
    # Detect two-column layout: many blocks that live entirely in the left or right half.
    left = [b for b in blocks if b["bbox"][2] < w * 0.55]
    right = [b for b in blocks if b["bbox"][0] > w * 0.45]
    if len(left) >= 3 and len(right) >= 3 and len(left) + len(right) >= 0.8 * len(blocks):
        side = {id(b) for b in left} | {id(b) for b in right}  # identity set: `in list` on dicts was O(n^2)
        mid = [b for b in blocks if id(b) not in side]
        key = lambda b: (b["bbox"][1], b["bbox"][0])  # noqa: E731
        return sorted(left, key=key) + sorted(right, key=key) + sorted(mid, key=key), truncated
    return sorted(blocks, key=lambda b: (round(b["bbox"][1] / 3), b["bbox"][0])), truncated


_PAGE_LABEL = re.compile(r"^\W*(?:page|pg\.?|p\.)\s*\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\W*$", re.I)
_BARE_PAGE_NO = re.compile(r"^\W{0,3}\d{1,4}\W{0,3}$")
_CONNECTIVES = frozenset("and or of to the a an in on for with by as at from that which if but nor than whose where when shall may must will not".split())


def _is_page_number(text: str, ypos: float | None) -> bool:
    """A whole block that is a page number: 'Page 3 of 10' (any repeat count) or a bare number in the top 8% / bottom 12% of the
    page. Pure digits elsewhere (list numbers, amounts) are never touched; blocks without a position (OCR) only match the labelled form."""
    t = text.strip()
    if not t or "\n" in t and len(t) > 40:
        return False
    if _PAGE_LABEL.match(t):
        return True
    return bool(ypos is not None and _BARE_PAGE_NO.match(t) and (ypos >= 0.88 or ypos <= 0.08))


def _drop_page_numbers(per_page: list[list[dict]]) -> int:
    """Remove page-number blocks at the first/last position of each page; never blanks a page. Returns blocks removed."""
    n = 0
    for blocks in per_page:
        if len(blocks) < 2:
            continue
        for pos in (-1, 0):
            if len(blocks) >= 2 and _is_page_number(blocks[pos]["text"], blocks[pos].get("ypos")):
                del blocks[pos]
                n += 1
    return n


def _strip_running_headers(per_page: list[list[dict]], n_pages: int) -> set[str]:
    """Repeated first/last lines on most pages (page headers, footers, page numbers) are removed."""
    if n_pages < 3:
        return set()
    counts: dict[str, int] = {}
    for blocks in per_page:
        seen = set()
        if len(blocks) < 3:
            continue  # too few blocks to tell a running header/footer from content
        for b in blocks[:1] + blocks[-1:]:
            t = re.sub(r"\d+", "#", b["text"].strip())
            if len(t) < 120 and t not in seen:
                seen.add(t)
                counts[t] = counts.get(t, 0) + 1
    return {t for t, c in counts.items() if c >= max(3, int(0.6 * n_pages))}


def _continues(prev: str, text: str) -> bool:
    """Is `text` (first block of a new page) the continuation of `prev` (last block of the previous page, no sentence end)?
    Lowercase start is the strong signal. A capitalised start only counts when the previous block clearly stops mid-phrase
    (ends in a comma, hyphen/dash or a function word) and the new block does not look like a heading/list/clause number."""
    if text[:1].islower():
        return True
    if not text[:1].isupper() or _LIST_START.match(text) or split_numbering(text)[0] is not None:
        return False
    last = re.findall(r"[A-Za-z]+|\S", prev.rstrip())[-1:] or [""]
    return prev.rstrip()[-1:] in ",-–—" or last[0].lower() in _CONNECTIVES


def build_units(page_blocks: list[dict]) -> list[DocUnit]:
    """Turn ordered page blocks into units: assign headings, merge paragraphs split across page breaks,
    and track the heading path. Pure function (unit tested without a PDF)."""
    units: list[DocUnit] = []
    path: list[tuple[int, str]] = []
    for b in page_blocks:
        text = re.sub(r"[ \t]+", " ", b["text"]).strip()
        if not text:
            continue
        kind = b.get("kind", "paragraph")
        prev = units[-1] if units else None
        # Paragraph continued across a page/column break: previous doesn't end a sentence, this starts lowercase.
        if (
            prev and kind == "paragraph" and prev.kind == "paragraph" and prev.page != b["page"]
            and not _TERMINAL.search(prev.text) and _continues(prev.text, text)
        ):
            prev.text = f"{prev.text}\n{text}"
            prev.page_end = b["page"]
            continue
        if kind == "paragraph" and _LIST_START.match(text):
            kind = "list"
        num, body = split_numbering(text)
        if kind == "heading":
            level = b.get("level", 1)
            while path and path[-1][0] >= level:
                path.pop()
            path.append((level, text if len(text) < 120 else text[:117] + "..."))
        section_id = num
        if section_id is None and kind != "heading" and path:
            m = re.match(r"^(\d+(?:\.\d+)*)", path[-1][1])
            section_id = m.group(1) if m else None
        units.append(DocUnit(
            idx=len(units), text=text, kind=kind, page=b["page"], page_end=b["page"],
            section_id=section_id, section_path=" › ".join(p[1] for p in path),
        ))
    return units


def extract_pdf(path: Path) -> ExtractionResult:
    cfg = get_settings()
    warnings: list[str] = []
    try:
        doc = fitz.open(path)
    except Exception as exc:  # corrupt / unsupported
        return ExtractionResult([], [], {}, [], "failed", f"Could not open PDF: {type(exc).__name__}")
    try:
        if doc.needs_pass:
            return ExtractionResult([], [], {}, [], "failed", "PDF is encrypted/password-protected; unsupported.")
        if doc.page_count == 0:
            return ExtractionResult([], [], {}, [], "failed", "PDF contains no pages.")
        if doc.page_count > cfg.max_pages:
            return ExtractionResult([], [], {}, [], "failed", f"PDF has {doc.page_count} pages; limit is {cfg.max_pages}.")
        meta = {k: v for k, v in (doc.metadata or {}).items() if v}
        pages, per_page, image_only, ocr_used = [], [], [], []
        ocr_missing = False  # set once Tesseract proves unavailable, so we neither retry per page nor mislead the user
        sizes: list[float] = []
        t_start, total_chars, truncated_at = time.monotonic(), 0, None
        for pno, page in enumerate(doc, start=1):
            if total_chars > MAX_TOTAL_CHARS or time.monotonic() - t_start > MAX_EXTRACT_SECONDS:
                truncated_at = pno
                break
            ocr = False
            blocks = []
            page_blocks, page_truncated = _page_blocks(page)
            if page_truncated:
                warnings.append(f"Page {pno} has more than {MAX_BLOCKS_PER_PAGE} text blocks; extraction of that page was truncated.")
            for b in page_blocks:
                text, size, bold = _block_text(b)
                blocks.append({"text": text, "size": size, "bold": bold, "table": _looks_like_table(b), "page": pno,
                               "ypos": b["bbox"][1] / max(page.rect.height, 1.0)})
            if sum(len(b["text"]) for b in blocks) < MIN_CHARS_PER_PAGE:
                if cfg.ocr_enabled and not ocr_missing:
                    ocr_blocks, ok, available = _ocr_page(page, pno, cfg.ocr_language)
                    if not available:
                        ocr_missing = True
                    elif ok:  # never replace an existing (short) text layer with an empty OCR result
                        blocks, ocr = ocr_blocks, True
                        ocr_used.append(pno)
                if not blocks:
                    image_only.append(pno)
            per_page.append(blocks)
            total_chars += sum(len(b["text"]) for b in blocks)
            pages.append({"page_no": pno, "text": "\n".join(b["text"] for b in blocks), "ocr": ocr})
        if truncated_at:
            warnings.append(f"Extraction stopped before page {truncated_at} (size/time safety limit); later pages are NOT included.")
        noise = _strip_running_headers(per_page, len(per_page))
        if noise:
            warnings.append("Removed repeated page headers/footers from extraction.")
            for blocks in per_page:
                kept = [b for b in blocks if re.sub(r"\d+", "#", b["text"].strip()) not in noise]
                if kept:  # never blank a page: a lone block that matches is content, not a header
                    blocks[:] = kept
        if _drop_page_numbers(per_page):
            warnings.append("Removed page-number lines (e.g. 'Page 3 of 10') from extraction.")
        for blocks in per_page:  # body font size from the kept blocks only (running headers are often smaller/larger)
            for b in blocks:
                sizes.extend([b["size"]] * max(1, len(b["text"]) // 40))
        body_size = statistics.median(sizes) if sizes else 10.0
        flat = []
        for blocks in per_page:
            for b in blocks:
                if b["table"]:
                    b["kind"] = "table"
                elif b["size"] >= body_size * 1.15 or (b["bold"] and len(b["text"]) < 100 and "\n" not in b["text"]):
                    b["kind"] = "heading"
                    b["level"] = 1 if b["size"] >= body_size * 1.4 else 2
                flat.append(b)
        units = build_units(flat)
        total_pages = len(pages)
        if ocr_used:
            warnings.append(f"OCR was applied to page(s) {_fmt(ocr_used)}; OCR text may contain recognition errors.")
        if ocr_missing:
            warnings.append("OCR_ENABLED=true but Tesseract is not installed or not usable (e.g. `brew install tesseract`); no OCR was performed.")
        if image_only:
            hint = "Install Tesseract to extract them." if ocr_missing else "Enable OCR (OCR_ENABLED=true, requires Tesseract) to extract them."
            warnings.append(f"No extractable text on page(s) {_fmt(image_only)} (likely scanned images). {hint}")
        status = "ready"
        if not units:
            status = "needs_ocr"
            warnings.append("The document yielded no text. It is probably a scanned/image-only PDF and cannot be compared without OCR.")
        elif len(image_only) > 0.3 * total_pages:
            warnings.append("Extraction is incomplete: more than 30% of pages have no text. Comparison results will be partial.")
        return ExtractionResult(pages, units, meta, warnings, status)
    finally:
        doc.close()


def _fmt(pages: list[int]) -> str:
    return ", ".join(map(str, pages[:12])) + ("…" if len(pages) > 12 else "")


def _ocr_page(page: "fitz.Page", pno: int, lang: str) -> tuple[list[dict], bool, bool]:
    """Optional OCR via PyMuPDF's Tesseract integration. Returns (blocks, ok, tesseract_available).
    NOTE: only the Tesseract-missing path is verified in this repo's tests; real OCR quality is untested."""
    try:
        side_pt = max(page.rect.width, page.rect.height, 1.0)
        dpi = int(max(20, min(200, MAX_OCR_PIXELS_SIDE / side_pt * 72)))  # huge MediaBox must not allocate a giant pixmap
        tp = page.get_textpage_ocr(language=lang, dpi=dpi, full=True)
        d = page.get_text("dict", textpage=tp)
    except Exception:
        return [], False, False
    out = []
    for b in d["blocks"]:
        if b["type"] == 0:
            text, size, bold = _block_text(b)
            if text:
                out.append({"text": text, "size": size, "bold": bold, "table": False, "page": pno})
    return out, bool(out), True


# ---------------------------------------------------------------------------------------------------------------
# Process isolation: a hostile/huge PDF must not be able to exhaust the API server's memory or hang it.
# ---------------------------------------------------------------------------------------------------------------
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _rss_mb(pid: int) -> float | None:
    """Resident set size of `pid` in MB, or None if unavailable. /proc on Linux, `ps` elsewhere (macOS)."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
        return None
    except FileNotFoundError:
        pass
    except OSError:
        return None
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=2).stdout.strip()
        return int(out) / 1024.0 if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _failed(msg: str) -> ExtractionResult:
    return ExtractionResult([], [], {}, [], "failed", msg)


def extract_pdf_isolated(path: Path, timeout_s: float | None = None, max_rss_mb: int | None = None) -> ExtractionResult:
    """Run extract_pdf in a fresh child interpreter. Enforces a hard wall-clock timeout and a resident-memory ceiling
    (watchdog polling every ~100 ms; on Linux the child additionally gets RLIMIT_AS). Returns the normal ExtractionResult,
    or status 'failed' with a clear error on timeout / memory kill / crash. The child is always killed and reaped."""
    cfg = get_settings()
    timeout = float(timeout_s if timeout_s is not None else cfg.extract_timeout_s)
    rss_cap = int(max_rss_mb if max_rss_mb is not None else cfg.extract_max_rss_mb)
    fd, out_name = tempfile.mkstemp(prefix="extract-", suffix=".pkl", dir=str(cfg.upload_dir))
    os.close(fd)
    env = {**os.environ, "DOCUDIFF_CHILD_BUDGET_S": str(max(1.0, timeout * 0.8)), "DOCUDIFF_CHILD_RSS_MB": str(rss_cap)}
    proc = subprocess.Popen([sys.executable, "-m", "app.services.pdf_extraction", str(path), out_name],
                            cwd=str(_BACKEND_ROOT), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0, reason, peak = time.monotonic(), None, 0.0
    try:
        while True:
            try:
                proc.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() - t0 > timeout:
                reason = f"PDF processing exceeded the {timeout:g} s time limit and was stopped."
                break
            rss = _rss_mb(proc.pid)
            if rss is not None:
                peak = max(peak, rss)
                if rss > rss_cap:
                    reason = f"PDF processing exceeded the {rss_cap} MB memory limit and was stopped."
                    break
        if reason:
            proc.kill()
            proc.wait()
            return _failed(reason)
        if proc.returncode != 0:
            hint = " (killed, likely out of memory)" if proc.returncode in (-9, -11) or proc.returncode == 137 else ""
            return _failed(f"PDF parser process crashed (exit code {proc.returncode}){hint}.")
        try:
            with open(out_name, "rb") as f:
                res = pickle.load(f)  # written by our own child a moment ago, in a 0600 temp file we created
        except Exception:  # noqa: BLE001
            return _failed("PDF parser returned no result.")
        return res if isinstance(res, ExtractionResult) else _failed("PDF parser returned an invalid result.")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        try:
            os.unlink(out_name)
        except OSError:
            pass


def _child_main(argv: list[str]) -> int:
    global MAX_EXTRACT_SECONDS
    try:
        MAX_EXTRACT_SECONDS = float(os.environ.get("DOCUDIFF_CHILD_BUDGET_S", MAX_EXTRACT_SECONDS))
        if sys.platform.startswith("linux"):  # address-space cap (unreliable on macOS, so the parent's RSS watchdog is the primary guard)
            import resource
            mb = int(os.environ.get("DOCUDIFF_CHILD_RSS_MB", "1500"))
            lim = (mb + 1024) * 1024 * 1024  # headroom: virtual size always exceeds RSS (shared libs, mmap)
            resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
        res = extract_pdf(Path(argv[1]))
    except MemoryError:
        res = _failed("PDF processing ran out of memory.")
    except BaseException as exc:  # noqa: BLE001 - report instead of dying silently
        res = _failed(f"PDF parsing failed: {type(exc).__name__}")
    with open(argv[2], "wb") as f:
        pickle.dump(res, f, protocol=pickle.HIGHEST_PROTOCOL)
    return 0


if __name__ == "__main__":
    sys.exit(_child_main(sys.argv))
