from app.services.pdf_extraction import build_units, extract_pdf
from app.services.synthetic import make_pdf, make_scanned_pdf


def test_extracts_headings_paragraphs_pages(tmp_path):
    p = make_pdf(tmp_path / "a.pdf", [("h1", "1. Scope"), ("p", "1.1 This policy applies to everyone."),
                                       ("h2", "2. Fees"), ("p", "2.1 The fee is 5%.")], title="Demo")
    r = extract_pdf(p)
    assert r.status == "ready" and r.metadata.get("title") == "Demo"
    kinds = [u.kind for u in r.units]
    assert kinds == ["heading", "paragraph", "heading", "paragraph"]
    assert r.units[1].section_id == "1.1" and r.units[1].section_path == "1. Scope"
    assert r.pages[0]["text"]


def test_paragraph_split_across_pages_is_merged(tmp_path):
    long = "2.1 The institution shall verify the identity of each customer using an officially valid document and keep the records for the period required by law without any exception whatsoever, " * 3 + "end."
    p = make_pdf(tmp_path / "b.pdf", [("h1", "Heading"), ("p", long)], lines_per_page=4)
    r = extract_pdf(p)
    paras = [u for u in r.units if u.kind == "paragraph"]
    assert len(paras) == 1 and paras[0].page == 1 and paras[0].page_end == 2


def test_scanned_pdf_is_detected_not_empty_document(tmp_path):
    r = extract_pdf(make_scanned_pdf(tmp_path / "s.pdf"))
    assert r.status == "needs_ocr" and r.units == [] and any("scanned" in w.lower() for w in r.warnings)


def test_corrupt_and_encrypted(tmp_path):
    bad = tmp_path / "bad.pdf"; bad.write_bytes(b"%PDF-1.4 not really a pdf")
    assert extract_pdf(bad).status == "failed"
    import pymupdf
    d = pymupdf.open(make_pdf(tmp_path / "e0.pdf", [("p", "secret text here")]))
    d.save(tmp_path / "e.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    r = extract_pdf(tmp_path / "e.pdf")
    assert r.status == "failed" and "encrypted" in r.error.lower()


def test_build_units_continuation_rule():
    blocks = [{"text": "The clause continues", "page": 1}, {"text": "on the next page.", "page": 2}]
    u = build_units(blocks)
    assert len(u) == 1 and u[0].page_end == 2
