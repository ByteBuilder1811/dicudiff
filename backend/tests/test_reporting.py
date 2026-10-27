"""Export content tests (PDF / HTML / Markdown) built from in-memory comparison data, no HTTP or DB needed."""
from pathlib import Path
from types import SimpleNamespace as N

import pymupdf
import pytest

from app.services import reporting as r


def ch(**k):
    base = dict(category="MODIFIED", old_page=1, new_page=2, old_section="2.1", new_section="2.1", match_method="fuzzy", confidence=0.9,
                flags=["amount_changed"], explanation="why", old_text="old", new_text="new", inline_diff=None, ai_explanation=None,
                review_status="done", seq=0)
    base.update(k)
    return N(**base)


def data(rows, label_a="Version A", label_b="Version B", limits=("lim",)):
    comp = N(id="c" * 32, label_a=label_a, label_b=label_b, doc_a_id=1, doc_b_id=2, limitations=[])
    counts = {c: 0 for c in r.ORDER}
    for x in rows:
        counts[x.category] += 1
    return dict(comp=comp, a=N(original_name="a.pdf", page_count=1, sha256="a" * 64), b=N(original_name="b.pdf", page_count=1, sha256="b" * 64),
                rows=rows, counts=counts, risk=[], pending=[], generated="now", limits=list(limits))


def pdf_text(d, tmp_path: Path):
    p = tmp_path / "x.pdf"
    r.to_pdf(d, p)
    doc = pymupdf.open(p)
    return doc, "\n".join(pg.get_text() for pg in doc)


def test_section_labels_are_escaped_in_html_and_markdown():
    evil = "<script>alert(1)</script>"
    d = data([ch(old_section=evil, new_section=evil, old_text=evil, new_text=evil, explanation=evil)], label_a=evil, label_b="B <b>x</b>")
    html_ = r.to_html(d)
    assert "<script>" not in html_ and "&lt;script&gt;" in html_
    md = r.to_markdown(d)
    assert "<script>" not in md and "<b>" not in md


def test_markdown_table_cells_survive_pipes_in_section_labels():
    md = r.to_markdown(data([ch(old_section="a | b")]))
    row = next(line for line in md.splitlines() if line.startswith("| 1 |"))
    assert row.count("|") - row.count("\\|") == 8  # 7 columns -> 8 unescaped delimiters


def test_pdf_has_summary_change_table_detail_and_limitations(tmp_path):
    doc, t = pdf_text(data([ch(), ch(category="REMOVED", new_text=None, old_text="gone clause")]), tmp_path)
    for needle in ("Executive summary", "Documents", "Change table", "Detailed changes", "Comparison limitations", "gone clause"):
        assert needle in t
    assert "<b>" not in t  # no raw markup leaks into the document


def test_pdf_markup_in_user_text_is_not_interpreted(tmp_path):
    doc, t = pdf_text(data([ch(old_section="<b>x</font>", old_text="<i>a</i> & <unclosed", explanation="1 < 2 && 3 > 2")]), tmp_path)
    assert "<i>a</i> & <unclosed" in t.replace("\n", " ") and "1 < 2 && 3 > 2" in t.replace("\n", " ")


def test_pdf_long_unbroken_text_stays_inside_margins_and_marks_truncation(tmp_path):
    doc, t = pdf_text(data([ch(old_text="X" * 400, new_text="word " * 600)]), tmp_path)
    for page in doc:
        for x0, _, x1, _, *_ in page.get_text("words"):
            assert x0 >= 0 and x1 <= page.rect.width - 20, "text overflows the right margin"
    assert "[truncated]" in t  # truncation is explicit, never silent


def test_pdf_non_latin_text_is_not_silently_garbled(tmp_path):
    doc, t = pdf_text(data([ch(old_text="Fee 500 and 日本語 text")]), tmp_path)
    if "日" in r._pdf_font({"日", "本", "語"})[4]:  # a Unicode font exists on this machine -> original characters kept
        assert "日本語" in t
    else:  # fallback: visible '?' plus a limitation note, not wrong glyphs
        assert "?" in t and "no installed font covers" in t


def test_pdf_many_changes_paginate(tmp_path):
    doc, _ = pdf_text(data([ch(old_text=f"clause {i} " * 30, seq=i) for i in range(150)]), tmp_path)
    assert doc.page_count > 3
