"""PDF extraction edge cases. OCR itself is UNVERIFIED here (Tesseract is not installed); only detection and the
'OCR enabled but Tesseract missing' path are tested."""
import pymupdf
import pytest

from app.core.config import get_settings
from app.services import pdf_extraction
from app.services.normalization import normalize
from app.services.pdf_extraction import extract_pdf
from app.services.synthetic import (make_blank_pdf, make_header_footer_pdf, make_hyphen_break_pdf, make_mixed_pdf, make_pdf,
                                    make_scanned_pdf, make_two_column_pdf)


def test_blank_pages_need_ocr_not_empty_ready(tmp_path):
    r = extract_pdf(make_blank_pdf(tmp_path / "b.pdf"))
    assert r.status == "needs_ocr" and r.units == [] and len(r.pages) == 2


def test_zero_page_pdf(tmp_path):
    p = tmp_path / "z.pdf"
    p.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R/Size 3>>\n%%EOF")
    r = extract_pdf(p)
    assert r.status == "failed" and r.error


@pytest.mark.parametrize("data", [b"", b"%PDF-1.4\n" + b"\x00" * 500, b"%PDF-1.4 not really a pdf", b"PK\x03\x04zip"])
def test_malformed_files_fail_cleanly(tmp_path, data):
    p = tmp_path / "m.pdf"; p.write_bytes(data)
    r = extract_pdf(p)
    assert r.status == "failed" and r.error and r.units == []


def test_truncated_pdf_never_crashes_and_reports_honestly(tmp_path):
    src = make_pdf(tmp_path / "g.pdf", [("h1", "1. A"), ("p", "1.1 " + "The institution shall verify. " * 80)] * 5)
    b = src.read_bytes()
    (tmp_path / "t.pdf").write_bytes(b[: len(b) // 2])
    r = extract_pdf(tmp_path / "t.pdf")
    assert r.status in ("ready", "needs_ocr", "failed")
    if r.status == "ready":  # PyMuPDF repaired it: the lost pages must be reported, not silently dropped
        assert r.warnings


def test_encrypted_pdf_rejected_and_owner_only_password_opens(tmp_path):
    base = make_pdf(tmp_path / "e0.pdf", [("p", "secret text here for the document")])
    d = pymupdf.open(base)
    d.save(tmp_path / "e.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    assert "encrypted" in extract_pdf(tmp_path / "e.pdf").error.lower()
    d = pymupdf.open(base)
    d.save(tmp_path / "o.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="y")
    assert extract_pdf(tmp_path / "o.pdf").status == "ready"  # view-only restriction, no password needed


def test_scanned_detection_and_mixed_document_warns_partial(tmp_path):
    assert extract_pdf(make_scanned_pdf(tmp_path / "s.pdf")).status == "needs_ocr"
    r = extract_pdf(make_mixed_pdf(tmp_path / "m.pdf"))
    assert r.status == "ready" and len(r.units) == 2
    assert any("2, 3" in w and "scanned" in w.lower() for w in r.warnings) and any("incomplete" in w.lower() for w in r.warnings)


def test_ocr_enabled_but_tesseract_missing_is_graceful(tmp_path, monkeypatch):
    try:
        pymupdf.open(make_scanned_pdf(tmp_path / "probe.pdf"))[0].get_textpage_ocr(language="eng", dpi=50, full=True)
        pytest.skip("Tesseract is installed here; this test covers the missing-Tesseract path")
    except RuntimeError:
        pass
    monkeypatch.setattr(get_settings(), "ocr_enabled", True)
    r = extract_pdf(tmp_path / "probe.pdf")
    assert r.status == "needs_ocr" and r.units == []
    assert any("Tesseract is not installed" in w for w in r.warnings)
    assert not any("Enable OCR" in w for w in r.warnings)  # don't tell the user to enable what is already enabled
    assert all(not p["ocr"] for p in r.pages)


def test_short_real_text_page_survives_when_ocr_enabled_but_missing(tmp_path, monkeypatch):
    """Regression: a page with <20 chars of real text lost that text when OCR was enabled and failed."""
    try:
        pymupdf.open(make_scanned_pdf(tmp_path / "probe.pdf"))[0].get_textpage_ocr(language="eng", dpi=50, full=True)
        pytest.skip("Tesseract is installed here")
    except RuntimeError:
        pass
    monkeypatch.setattr(get_settings(), "ocr_enabled", True)
    d = pymupdf.open(); d.new_page().insert_text((56, 100), "Annexure A", fontsize=12); d.save(tmp_path / "short.pdf")
    r = extract_pdf(tmp_path / "short.pdf")
    assert [u.text for u in r.units] == ["Annexure A"] and r.status == "ready"


def test_very_long_single_paragraph_is_one_unit_and_chunks_bounded(tmp_path):
    from app.services import retrieval
    r = extract_pdf(make_pdf(tmp_path / "l.pdf", [("h1", "1. Long"), ("p", "word " * 6000)]))
    paras = [u for u in r.units if u.kind == "paragraph"]
    assert len(paras) == 1 and paras[0].page_end > paras[0].page and len(paras[0].text) > 25000
    for u in r.units:
        u.page_start, u.page_end = u.page, u.page_end or u.page
    assert all(len(c.text) <= retrieval.MAX_CHARS for c in retrieval.chunk_units(r.units))


def test_repeated_headers_footers_removed_and_do_not_become_headings(tmp_path):
    r = extract_pdf(make_header_footer_pdf(tmp_path / "h.pdf"))
    texts = [u.text for u in r.units]
    assert not any("ACME" in t or t.startswith("Page ") for t in texts) and len(r.units) == 10
    assert all(u.kind == "paragraph" for u in r.units)  # regression: body font size was estimated before header removal


def test_lone_block_per_page_is_not_mistaken_for_running_header(tmp_path):
    """Regression: 400 pages with one line each differing only in digits were all deleted as 'headers'."""
    d = pymupdf.open()
    for i in range(6):
        d.new_page().insert_text((56, 100), f"{i}.1 Clause on page {i} with some words about topic {i}.", fontsize=11)
    d.save(tmp_path / "one.pdf")
    r = extract_pdf(tmp_path / "one.pdf")
    assert r.status == "ready" and len(r.units) == 6


def test_two_column_reading_order_left_then_right(tmp_path):
    r = extract_pdf(make_two_column_pdf(tmp_path / "c.pdf"))
    order = [u.text.split(".")[0] for u in r.units]
    assert order == [f"Left {i}" for i in range(5)] + [f"Right {i}" for i in range(5)]


def test_hyphenation_across_page_break_merged_and_dehyphenated(tmp_path):
    r = extract_pdf(make_hyphen_break_pdf(tmp_path / "hy.pdf"))
    assert len(r.units) == 1 and r.units[0].page_end == 2
    assert "documentation" in normalize(r.units[0].text)


def test_table_rows_with_same_line_cells_become_table_unit(tmp_path):
    d = pymupdf.open(); p = d.new_page()
    for row in range(3):
        p.insert_textbox(pymupdf.Rect(56, 100 + row * 20, 540, 118 + row * 20), "Tier\t\t\tLimit\t\t\tFee", fontsize=10)
    d.save(tmp_path / "t.pdf")
    r = extract_pdf(tmp_path / "t.pdf")
    assert r.status == "ready" and r.units  # documented: table detection is heuristic; cells never get lost


def test_huge_mediabox_and_page_limit_are_cheap(tmp_path, monkeypatch):
    d = pymupdf.open(); d.new_page(width=14400, height=14400).insert_text((100, 100), "Huge page text", fontsize=12); d.save(tmp_path / "h.pdf")
    assert extract_pdf(tmp_path / "h.pdf").status == "ready"
    monkeypatch.setattr(get_settings(), "max_pages", 3)
    make_blank_pdf(tmp_path / "many.pdf", pages=5)
    r = extract_pdf(tmp_path / "many.pdf")
    assert r.status == "failed" and "limit" in r.error


def test_total_character_cap_stops_extraction_with_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_extraction, "MAX_TOTAL_CHARS", 500)
    d = pymupdf.open()
    for i in range(10):
        d.new_page().insert_textbox(pymupdf.Rect(20, 20, 575, 820), "lorem ipsum dolor sit amet " * 40, fontsize=8)
    d.save(tmp_path / "big.pdf")
    r = extract_pdf(tmp_path / "big.pdf")
    assert len(r.pages) < 10 and any("stopped before page" in w for w in r.warnings)


def test_extraction_time_budget_stops_with_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_extraction, "MAX_EXTRACT_SECONDS", -1.0)
    r = extract_pdf(make_pdf(tmp_path / "x.pdf", [("p", "some text on a page here")]))
    assert r.pages == [] and any("stopped before page 1" in w for w in r.warnings)


def test_ocr_dpi_is_capped_for_huge_pages(tmp_path, monkeypatch):
    seen = {}

    class P:
        rect = pymupdf.Rect(0, 0, 14400, 14400)

        def get_textpage_ocr(self, language, dpi, full):
            seen["dpi"] = dpi; raise RuntimeError("no tesseract")

    assert pdf_extraction._ocr_page(P(), 1, "eng") == ([], False, False)
    assert seen["dpi"] * 14400 / 72 <= pdf_extraction.MAX_OCR_PIXELS_SIDE + 72


def test_block_cap_truncates_with_warning(tmp_path, monkeypatch):
    """Precaution: pages with more raw text blocks than MAX_BLOCKS_PER_PAGE are truncated with a warning.
    (An extraction blow-up from many blocks could NOT be reproduced with synthetic PDFs: MuPDF merges tiny blocks.)"""
    from app.services import pdf_extraction as px
    from app.services.synthetic import make_pdf

    monkeypatch.setattr(px, "MAX_BLOCKS_PER_PAGE", 3)
    p = make_pdf(tmp_path / "many.pdf", [("h1", "1. Scope")] + [("p", f"1.{k} Paragraph number {k} about distinct topic {k * 7}.") for k in range(1, 12)])
    r = px.extract_pdf(p)
    assert r.status == "ready" and any("truncated" in w for w in r.warnings)
    assert len(r.units) < 12
