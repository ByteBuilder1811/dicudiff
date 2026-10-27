"""Generate small synthetic PDFs (used by tests, the benchmark and the bundled demo documents)."""
from pathlib import Path

import pymupdf as fitz

PAGE_W, PAGE_H, MARGIN = 595, 842, 56


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if fitz.get_text_length(trial, fontname=font, fontsize=size) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def make_pdf(path: Path, blocks: list[tuple[str, str]], lines_per_page: int = 44, title: str = "") -> Path:
    """blocks: ('h1'|'h2'|'p', text). Paragraphs may flow across page breaks (like real documents)."""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y, used = MARGIN, 0

    def new_page():
        nonlocal page, y, used
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        y, used = MARGIN, 0

    for kind, text in blocks:
        font, size, gap = {"h1": ("hebo", 18, 10), "h2": ("hebo", 13, 8)}.get(kind, ("helv", 10.5, 7))
        lines = _wrap(text, font, size, PAGE_W - 2 * MARGIN)
        y += gap
        for line in lines:
            if used >= lines_per_page or y > PAGE_H - MARGIN:
                new_page()
            page.insert_text((MARGIN, y), line, fontname=font, fontsize=size)
            y += size * 1.35
            used += 1 if kind == "p" else 2
    if title:
        doc.set_metadata({"title": title})
    doc.save(path)
    doc.close()
    return path


def make_scanned_pdf(path: Path, text: str = "Scanned page content") -> Path:
    """Image-only PDF (no text layer) for testing scanned-document detection."""
    src = fitz.open()
    p = src.new_page(width=300, height=300)
    p.insert_text((20, 100), text, fontsize=20)
    pix = p.get_pixmap(dpi=72)
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.insert_image(page.rect, pixmap=pix)
    doc.save(path)
    return path


def make_blank_pdf(path: Path, pages: int = 2) -> Path:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(path)
    return path


def make_mixed_pdf(path: Path, scanned_pages: int = 2) -> Path:
    """One real text page followed by image-only pages."""
    tmp = path.with_suffix(".tmp.pdf")
    text_pdf = make_pdf(tmp, [("h1", "1. Text"), ("p", "1.1 A normal text paragraph that can be extracted.")])
    scan = make_scanned_pdf(path.with_suffix(".scan.pdf"))
    out = fitz.open()
    out.insert_pdf(fitz.open(text_pdf))
    for _ in range(scanned_pages):
        out.insert_pdf(fitz.open(scan))
    out.save(path)
    return path


def make_header_footer_pdf(path: Path, pages: int = 5) -> Path:
    """Every page: running header, one body clause, 'Page n of N' footer (body font larger than header/footer)."""
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page(width=PAGE_W, height=PAGE_H)
        p.insert_text((MARGIN, 40), "ACME Corp Confidential", fontsize=9)
        p.insert_text((MARGIN, 800), f"Page {i + 1} of {pages}", fontsize=9)
        p.insert_text((MARGIN, 120), f"{i + 1}.1 Clause number {i} says something specific about topic {i}.", fontsize=11)
        p.insert_text((MARGIN, 160), f"{i + 1}.2 A second clause number {i} about a different subject {i}.", fontsize=11)
    doc.save(path)
    return path


def make_two_column_pdf(path: Path, rows: int = 5) -> Path:
    doc = fitz.open()
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    for i in range(rows):
        p.insert_textbox(fitz.Rect(56, 60 + i * 120, 280, 170 + i * 120), f"Left {i}. Left column paragraph number {i} that wraps over lines.", fontsize=10)
        p.insert_textbox(fitz.Rect(320, 60 + i * 120, 540, 170 + i * 120), f"Right {i}. Right column paragraph number {i} that wraps over lines.", fontsize=10)
    doc.save(path)
    return path


def make_hyphen_break_pdf(path: Path) -> Path:
    doc = fitz.open()
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    p.insert_text((MARGIN, 780), "2.1 The institution shall maintain full docu-", fontsize=11)
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    p.insert_text((MARGIN, 60), "mentation of every transaction.", fontsize=11)
    doc.save(path)
    return path
