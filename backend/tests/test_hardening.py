"""Hardening tests: process-isolated PDF parsing, numeric tokenization, rupee glyph rendering, page-number stripping,
optional API token."""
import time
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.core import limits
from app.core.config import get_settings
from app.main import app
from app.services import reporting as rep
from app.services import retrieval
from app.services.pdf_extraction import build_units, extract_pdf, extract_pdf_isolated
from tests.helpers import V1, pdf
from tests.test_reporting import ch, data, pdf_text

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset():
    limits._hits.clear()
    yield
    limits._hits.clear()


def _leftover_tmp():
    return list(get_settings().upload_dir.glob("extract-*"))


# ------------------------------------------------------------------ 1. isolation
def test_isolated_extraction_matches_in_process(tmp_path):
    p = pdf(tmp_path, "a.pdf", V1)
    a, b = extract_pdf(p), extract_pdf_isolated(p, timeout_s=60)
    assert b.status == a.status == "ready"
    assert [u.text for u in b.units] == [u.text for u in a.units]
    assert [pg["text"] for pg in b.pages] == [pg["text"] for pg in a.pages]
    assert _leftover_tmp() == []


def test_isolated_corrupt_pdf_reports_failure_not_crash(tmp_path):
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"%PDF-1.4\ngarbage")
    r = extract_pdf_isolated(p, timeout_s=60)
    assert r.status == "failed" and r.error


def test_isolated_timeout_kills_child(tmp_path):
    p = pdf(tmp_path, "a.pdf", V1)
    t0 = time.monotonic()
    r = extract_pdf_isolated(p, timeout_s=0.05)  # shorter than interpreter start-up: deterministic
    assert r.status == "failed" and "time limit" in r.error
    assert time.monotonic() - t0 < 10
    assert _leftover_tmp() == []


def test_isolated_memory_watchdog_kills_child(tmp_path):
    p = pdf(tmp_path, "a.pdf", V1)
    r = extract_pdf_isolated(p, timeout_s=60, max_rss_mb=1)  # any Python interpreter exceeds 1 MB resident
    assert r.status == "failed" and "memory limit" in r.error
    assert _leftover_tmp() == []


def test_upload_survives_killed_parser_and_server_stays_responsive(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "extract_timeout_s", 0.05)
    p = pdf(tmp_path, "a.pdf", V1)
    r = client.post("/api/documents/upload", files={"file": ("a.pdf", p.read_bytes(), "application/pdf")})
    assert r.status_code == 201
    doc = r.json()["document"]
    assert doc["status"] == "failed" and "time limit" in (doc["error"] or "")
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/documents").status_code == 200


def test_upload_uses_isolated_parser_by_default(tmp_path, monkeypatch):
    from app.services import pipeline
    calls = []
    real = pipeline.extract_pdf_isolated
    monkeypatch.setattr(pipeline, "extract_pdf_isolated", lambda p: calls.append(p) or real(p))
    p = pdf(tmp_path, "iso.pdf", V1)
    r = client.post("/api/documents/upload", files={"file": ("iso.pdf", p.read_bytes(), "application/pdf")})
    assert r.status_code == 201 and r.json()["document"]["status"] == "ready" and len(calls) == 1


# ------------------------------------------------------------------ 2. numbers
@pytest.mark.parametrize("s", ["10,00,000", "1,000,000", "1000000", "10,00,000.00", "1,000,000.00", "10 lakh", "10 Lakhs"])
def test_equivalent_amounts_tokenize_identically(s):
    assert retrieval.tokenize(f"fee of {s} only")[-3:-1] == ["of", "1000000"] or "1000000" in retrieval.tokenize(s)
    assert retrieval.tokenize(s) == ["1000000"]


def test_non_grouping_commas_and_decimals_are_preserved():
    assert retrieval.tokenize("1,2,3") == ["1,2,3"]
    assert retrieval.tokenize("12,34") == ["12,34"]
    assert retrieval.tokenize("99,999.50") == ["99999.50"]
    assert retrieval.tokenize("clause 2.0") == ["clause", "2.0"]
    assert retrieval.tokenize("5 crore") == ["50000000"]
    assert retrieval.tokenize("2.5%") == ["2.5%"]


def test_bm25_finds_amount_across_formats():
    docs = [retrieval.tokenize(t) for t in ("Penalty is Rs 10,00,000 per breach", "Notice period is 30 days", "Fee 5,000 monthly")]
    scores = retrieval.BM25(docs).get_scores(retrieval.tokenize("1,000,000"))
    assert scores.argmax() == 0 and scores[0] > 0 and scores[1] == 0


def test_embedding_text_is_not_normalised():
    # normalisation is applied only inside tokenize(); chunk/embedding text keeps the document's own formatting
    assert retrieval.normalize_numbers("10,00,000") == "1000000"
    src = Path(retrieval.__file__).read_text()
    assert src.count("normalize_numbers(") == 2  # definition + the single use inside tokenize()


# ------------------------------------------------------------------ 3. rupee glyph
def test_pdf_rupee_rendered_or_substituted_never_a_box(tmp_path):
    needed = {"₹"}
    uni = rep._pdf_font(needed)[3] and "₹" in rep._pdf_font(needed)[4]
    if not uni:
        pytest.skip("no installed font contains U+20B9; the substitution path is tested separately")
    doc, t = pdf_text(data([ch(old_text="Fee ₹5,00,000 payable", new_text="Fee ₹6,00,000 payable")]), tmp_path)
    assert "₹5,00,000" in t.replace(" ", "")


def test_pdf_rupee_substitution_when_no_font_has_it(tmp_path, monkeypatch):
    monkeypatch.setattr(rep, "_FONT_CANDIDATES", [])
    monkeypatch.setattr(rep, "_fc_candidates", lambda ch: [])
    doc, t = pdf_text(data([ch(old_text="Fee ₹500 due", new_text="Fee ₹600 due")]), tmp_path)
    flat = t.replace("\n", " ")
    assert "Rs.500" in flat.replace(" ", "") and "₹" not in flat
    assert "rupee sign" in flat  # limitation line present


def test_pdf_font_selection_prefers_full_coverage():
    reg, bold, ital, uni, covered = rep._pdf_font({"₹"})
    if uni:
        assert "₹" in covered
    else:
        assert covered == set()


# ------------------------------------------------------------------ 4. page numbers / continuation
def _two_page(path: Path, footer1: str, footer2: str, extra_bottom=False) -> Path:
    doc = pymupdf.open()
    for i, foot in enumerate((footer1, footer2)):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((72, 100), f"{i + 1}.1 This clause {i} states an obligation about topic {i} in full.", fontsize=11)
        pg.insert_text((72, 140), f"{i + 1}.2 A second clause {i} concerns a different subject {i}.", fontsize=11)
        pg.insert_text((290, 810), foot, fontsize=9)
    doc.save(path)
    return path


def test_page_n_of_m_footer_dropped_even_with_two_pages(tmp_path):
    r = extract_pdf(_two_page(tmp_path / "p.pdf", "Page 1 of 2", "Page 2 of 2"))
    assert not any("Page " in u.text for u in r.units) and len(r.units) == 4
    assert any("page-number" in w for w in r.warnings)


def test_bare_page_number_at_page_bottom_dropped_but_body_numbers_kept(tmp_path):
    r = extract_pdf(_two_page(tmp_path / "p.pdf", "1", "2"))
    assert [u.text for u in r.units if u.text.strip() in ("1", "2")] == []
    assert len(r.units) == 4
    doc = pymupdf.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((72, 100), "Clause about the total below.", fontsize=11)
    pg.insert_text((72, 300), "500", fontsize=11)  # mid-page bare number is content
    pg.insert_text((72, 340), "Closing sentence of the page here.", fontsize=11)
    doc.save(tmp_path / "q.pdf")
    assert any(u.text.strip() == "500" for u in extract_pdf(tmp_path / "q.pdf").units)


def _blk(text, page, kind="paragraph"):
    return {"text": text, "page": page, "kind": kind}


def test_continuation_across_page_break_capital_after_comma_or_connective():
    u = build_units([_blk("The Borrower shall repay the loan together with interest and", 1), _blk("Charges as set out in Schedule 2.", 2)])
    assert len(u) == 1 and u[0].page_end == 2
    u = build_units([_blk("Payments are due on the first day of each month,", 1), _blk("Subject to clause 4 below.", 2)])
    assert len(u) == 1


def test_continuation_is_conservative():
    # sentence-final previous block, headings, clause numbers and list items never merge on a capital letter
    assert len(build_units([_blk("This ends here.", 1), _blk("Next sentence begins.", 2)])) == 2
    assert len(build_units([_blk("Dangling text without end", 1), _blk("Another unrelated paragraph.", 2)])) == 2
    assert len(build_units([_blk("Payments shall be made by", 1), _blk("2.1 New clause", 2)])) == 2
    assert len(build_units([_blk("Payments shall be made by", 1), _blk("Definitions", 2, kind="heading")])) == 2
    assert len(build_units([_blk("Items are as follows and", 1), _blk("- Bullet one", 2)])) == 2
    assert len(build_units([_blk("lowercase start continues the", 1), _blk("previous page text", 2)])) == 1  # old behaviour kept


# ------------------------------------------------------------------ 7. API token
def test_token_off_by_default_allows_everything():
    assert get_settings().docudiff_api_token == ""
    assert client.get("/api/documents").status_code == 200


def test_token_on_requires_bearer_except_health(monkeypatch):
    monkeypatch.setattr(get_settings(), "docudiff_api_token", "s3cret-token")
    assert client.get("/api/health").status_code == 200
    r = client.get("/api/documents")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "unauthorized" and r.headers["www-authenticate"] == "Bearer"
    assert client.get("/api/documents", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/documents", headers={"Authorization": "s3cret-token"}).status_code == 401
    assert client.get("/api/documents", headers={"Authorization": "Basic s3cret-token"}).status_code == 401
    assert client.get("/api/documents", headers={"Authorization": "Bearer s3cret-token"}).status_code == 200
    assert client.post("/api/documents/upload", files={"file": ("a.pdf", b"%PDF-1", "application/pdf")}).status_code == 401
    assert "s3cret" not in r.text
