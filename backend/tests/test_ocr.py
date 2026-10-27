"""Real OCR path (needs the Tesseract binary; skipped otherwise)."""
import shutil

import pytest

from app.core.config import get_settings
from app.services.pdf_extraction import extract_pdf
from app.services.synthetic import make_scanned_pdf

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")


def test_scanned_pdf_is_read_with_ocr_and_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "ocr_enabled", True)
    r = extract_pdf(make_scanned_pdf(tmp_path / "s.pdf", "Fee is 5 percent"))
    assert r.status == "ready" and r.units
    assert "fee" in " ".join(u.text for u in r.units).lower()
    assert [p["ocr"] for p in r.pages] == [True]
    assert any("OCR was applied" in w for w in r.warnings)


def test_scanned_pdf_without_ocr_is_not_an_empty_document(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "ocr_enabled", False)
    r = extract_pdf(make_scanned_pdf(tmp_path / "s.pdf", "Fee is 5 percent"))
    assert r.status == "needs_ocr" and not r.units
