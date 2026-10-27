"""Regression tests for the Agent D security / reliability audit (see docs/AGENT_D_FINDINGS.md)."""
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core import db as dbmod
from app.core import limits
from app.core.config import get_settings
from app.main import app, clean_stale_tmp, purge_expired
from app.models.tables import Change, Chunk, Comparison, Document, Page, Report, Unit
from app.services import pipeline
from tests.helpers import V1, V2, pdf

client = TestClient(app)


def _upload(path: Path, name=None, c=client):
    return c.post("/api/documents/upload", files={"file": (name or path.name, path.read_bytes(), "application/pdf")})


def _uploads():
    return sorted(p.name for p in get_settings().upload_dir.iterdir())


@pytest.fixture(autouse=True)
def clean_state():
    limits._hits.clear()
    for d in (get_settings().upload_dir, get_settings().report_dir):
        for f in d.glob("*"):
            f.unlink()
    yield
    limits._hits.clear()


# ---------------------------------------------------------------- upload
def test_oversize_upload_rejected_before_handler_by_content_length(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    called = []
    monkeypatch.setattr(pipeline, "ingest_pdf", lambda *a, **k: called.append(1))
    r = client.post("/api/documents/upload", files={"file": ("big.pdf", b"%PDF-1.4\n" + b"0" * (3 * 1024 * 1024), "application/pdf")})
    assert r.status_code == 413 and r.json()["detail"]["code"] == "too_large"
    assert not called and _uploads() == []


def test_oversize_chunked_upload_without_content_length_is_cut_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    boundary = "xBOUNDARYx"

    def body():
        yield (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.pdf\"\r\nContent-Type: application/pdf\r\n\r\n%PDF-1.4\n").encode()
        for _ in range(40):
            yield b"0" * (100 * 1024)  # 4 MB total, no Content-Length header (chunked)
        yield f"\r\n--{boundary}--\r\n".encode()

    r = client.post("/api/documents/upload", content=body(), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert r.status_code == 413
    assert _uploads() == []


def test_oversize_json_body_rejected():
    r = client.post("/api/comparisons", content=b"{" + b" " * (2 * 1024 * 1024) + b"}", headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_empty_file_and_bad_magic():
    r = client.post("/api/documents/upload", files={"file": ("e.pdf", b"", "application/pdf")})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "empty_file"
    r = client.post("/api/documents/upload", files={"file": ("x.pdf", b"<html>not pdf</html>", "application/pdf")})
    assert r.status_code == 415 and _uploads() == []


def test_hostile_filenames_are_display_only(tmp_path):
    p = pdf(tmp_path, "a.pdf", V1)
    r = _upload(p, name="..\\..\\evil\x00\x07name.pdf")
    assert r.status_code == 201
    doc = r.json()["document"]
    assert doc["original_name"].endswith("name.pdf") and "\\" not in doc["original_name"] and "/" not in doc["original_name"]
    stored = _uploads()
    assert len(stored) == 1 and stored[0].endswith(".pdf") and ".." not in stored[0] and "evil" not in stored[0]
    long = _upload(pdf(tmp_path, "b.pdf", V2), name="é" * 400 + ".pdf")
    assert long.status_code == 201 and len(long.json()["document"]["original_name"]) <= 255


def test_concurrent_identical_uploads_do_not_500_or_orphan_files(tmp_path):
    p = pdf(tmp_path, "r.pdf", V1)
    codes = []

    def go():
        codes.append(_upload(p, c=TestClient(app)).status_code)

    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert codes == [201] * 6
    with dbmod.session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Document)) == 1
    assert len(_uploads()) == 1


def test_failed_ingest_leaves_no_orphan_file(tmp_path, monkeypatch):
    def boom(_):
        raise RuntimeError("SECRET-DOC-TEXT")
    monkeypatch.setattr(pipeline, "extract_pdf_isolated", boom)
    c = TestClient(app, raise_server_exceptions=False)
    r = _upload(pdf(tmp_path, "a.pdf", V1), c=c)
    assert r.status_code == 500 and "SECRET" not in r.text
    assert _uploads() == []


def test_stale_tmp_files_cleaned(tmp_path):
    f = get_settings().upload_dir / "tmp-old"
    f.write_bytes(b"x")
    os.utime(f, (1, 1))
    fresh = get_settings().upload_dir / "tmp-fresh"
    fresh.write_bytes(b"x")
    assert clean_stale_tmp() == 1 and not f.exists() and fresh.exists()


# ------------------------------------------------------------ ids / headers
def test_ids_must_be_ascii_alphanumeric():
    for bad in ["١٢٣", "ａｂｃ", "a" * 41, "a%00b", "a.b"]:
        assert client.get(f"/api/documents/{bad}").status_code in (404, 422)
    assert client.get("/api/documents/١٢٣").status_code == 422
    assert client.get("/api/reports/..%2f..%2fetc%2fpasswd/download").status_code in (404, 422)


def test_security_headers_present():
    r = client.get("/api/stats")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


def test_dns_rebinding_host_rejected():
    r = client.get("/api/stats", headers={"Host": "evil.example.com"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_host"
    assert client.get("/api/stats", headers={"Host": "localhost:8000"}).status_code == 200
    assert client.get("/api/stats", headers={"Host": "[::1]:8000"}).status_code == 200


def test_cross_origin_post_blocked_but_get_and_ui_origin_allowed(tmp_path):
    p = pdf(tmp_path, "a.pdf", V1)
    r = client.post("/api/documents/upload", files={"file": ("a.pdf", p.read_bytes(), "application/pdf")}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "bad_origin" and _uploads() == []
    assert client.post("/api/documents/upload", files={"file": ("a.pdf", p.read_bytes(), "application/pdf")}, headers={"Origin": "http://localhost:5173"}).status_code == 201


def test_unhandled_exception_is_generic_and_not_logged_with_message(monkeypatch, caplog):
    from app.api import routes

    def boom():
        raise RuntimeError("SECRET-DOC-TEXT")
    monkeypatch.setattr(routes.local_llm, "status", boom)
    with caplog.at_level("DEBUG"):
        r = client.get("/api/health")  # default TestClient re-raises server errors: passing proves nothing leaks out
    assert r.status_code == 500 and r.json()["detail"]["code"] == "internal_error"
    assert "SECRET" not in r.text and "SECRET" not in caplog.text


# ----------------------------------------------------------- rate limiting
def test_polling_does_not_hit_the_default_limit():
    """Dashboard (80 req/min) + Workspace (60 req/min) + slack must stay below the read bucket."""
    s = get_settings()
    assert s.rate_limit_read_per_minute >= 3 * 140
    for _ in range(150):
        assert client.get("/api/stats").status_code == 200


def test_write_bucket_is_strict_and_separate(monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 2)
    codes = [client.post("/api/comparisons", json={"doc_a_id": "a", "doc_b_id": "b"}).status_code for _ in range(4)]
    assert 429 in codes[2:] and codes[:2] != [429, 429]
    assert client.get("/api/stats").status_code == 200  # reads unaffected


# -------------------------------------------------- lifecycle / deletion
def _two_docs(tmp_path):
    a = _upload(pdf(tmp_path, "a.pdf", V1)).json()["document"]["id"]
    b = _upload(pdf(tmp_path, "b.pdf", V2)).json()["document"]["id"]
    return a, b


def _run(a, b):
    cid = client.post("/api/comparisons", json={"doc_a_id": a, "doc_b_id": b, "options": {"semantic": False}}).json()["id"]
    return cid  # TestClient runs BackgroundTasks before returning


def test_delete_document_removes_everything(tmp_path):
    a, b = _two_docs(tmp_path)
    cid = _run(a, b)
    assert client.get(f"/api/comparisons/{cid}").json()["status"] == "completed"
    rid = client.post(f"/api/comparisons/{cid}/export", json={"format": "html"}).json()["id"]
    assert len(list(get_settings().report_dir.iterdir())) == 1
    assert client.delete(f"/api/documents/{a}").status_code == 200
    with dbmod.session_scope() as s:
        for model in (Comparison, Change, Report):
            assert s.scalar(select(func.count()).select_from(model)) == 0, model
        assert s.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == a)) == 0
        assert s.scalar(select(func.count()).select_from(Page).where(Page.document_id == a)) == 0
        assert s.scalar(select(func.count()).select_from(Unit).where(Unit.document_id == a)) == 0
    assert len(_uploads()) == 1 and list(get_settings().report_dir.iterdir()) == []
    assert client.get(f"/api/reports/{rid}/download").status_code == 404


def test_files_survive_a_rolled_back_delete(tmp_path):
    a, _ = _two_docs(tmp_path)
    before = _uploads()
    with pytest.raises(RuntimeError):
        with dbmod.session_scope() as s:
            pipeline.delete_document(s, s.get(Document, a))
            raise RuntimeError("commit never happens")
    assert _uploads() == before


def test_retention_purge_removes_only_expired(tmp_path, monkeypatch):
    a, b = _two_docs(tmp_path)
    with dbmod.session_scope() as s:
        s.get(Document, a).created_at = datetime.utcnow() - timedelta(days=40)
    monkeypatch.setattr(get_settings(), "retention_days", 30)
    assert purge_expired() == 1
    assert [d["id"] for d in client.get("/api/documents").json()["items"]] == [b]
    assert len(_uploads()) == 1
    monkeypatch.setattr(get_settings(), "retention_days", 0)
    assert purge_expired() == 0


def test_interrupted_comparisons_are_failed_on_startup_and_retryable(tmp_path):
    a, b = _two_docs(tmp_path)
    with dbmod.session_scope() as s:
        s.add_all([Comparison(doc_a_id=a, doc_b_id=b, status="running", stage="aligning", progress=0.3),
                   Comparison(doc_a_id=a, doc_b_id=b, status="queued")])
    assert pipeline.recover_interrupted() == 2
    items = client.get("/api/comparisons").json()["items"]
    assert {i["status"] for i in items} == {"failed"} and all(i["stage"] == "interrupted" for i in items)
    r = client.post(f"/api/comparisons/{items[0]['id']}/retry")
    assert r.status_code == 202


def test_job_for_deleted_comparison_does_not_raise(tmp_path):
    a, b = _two_docs(tmp_path)
    cid = _run(a, b)
    client.delete(f"/api/comparisons/{cid}")
    pipeline.run_comparison(cid)  # must not raise
    pipeline.run_comparison("0" * 32)


# ------------------------------------------------------------ report XSS
def _html_report_with(section: str, tmp_path):
    a, b = _two_docs(tmp_path)
    cid = _run(a, b)
    with dbmod.session_scope() as s:
        ch = s.scalars(select(Change).where(Change.comparison_id == cid, Change.category == "MODIFIED")).first()
        ch.old_section = section
        ch.old_text = "<img src=x onerror=alert(1)>"
        ch.inline_diff = {"old": [["del", "<script>alert(2)</script>"]], "new": [["ins", "ok"]]}
        ch.ai_explanation = {"model": "<m>", "observed_change": "<b>x</b>", "possible_implications": "<i>", "caveats": "<u>"}
    rid = client.post(f"/api/comparisons/{cid}/export", json={"format": "html"}).json()["id"]
    return client.get(f"/api/reports/{rid}/download")


def test_html_report_escapes_text_fields(tmp_path):
    r = _html_report_with("1.1", tmp_path)
    assert "<img src=x" not in r.text and "<script>alert(2)" not in r.text and "<b>x</b>" not in r.text
    assert r.headers["content-security-policy"] == "sandbox" and "attachment" in r.headers["content-disposition"]


def test_html_report_escapes_section_labels(tmp_path):
    r = _html_report_with("<script>alert(1)</script>", tmp_path)
    assert "<script>alert(1)</script>" not in r.text
