import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import V1, V2, pdf
from app.services.synthetic import make_scanned_pdf

client = TestClient(app)


def upload(path, label=None):
    with open(path, "rb") as f:
        return client.post("/api/documents/upload", files={"file": (path.name, f, "application/pdf")}, data={"label": label} if label else {})


def make_comparison(tmp_path, a=V1, b=V2):
    da = upload(pdf(tmp_path, "a.pdf", a), "Version A").json()["document"]["id"]
    db_ = upload(pdf(tmp_path, "b.pdf", b), "Version B").json()["document"]["id"]
    r = client.post("/api/comparisons", json={"doc_a_id": da, "doc_b_id": db_, "options": {"semantic": False}})
    assert r.status_code == 202
    cid = r.json()["id"]
    for _ in range(50):  # TestClient runs background tasks before returning, but poll to be safe
        c = client.get(f"/api/comparisons/{cid}").json()
        if c["status"] in ("completed", "failed"):
            break
        time.sleep(0.1)
    return cid, c, da, db_


def test_health_works_without_llm():
    r = client.get("/api/health").json()
    assert r["status"] == "ok" and r["llm"]["reachable"] is False and "hint" in r["llm"]


def test_upload_validation(tmp_path):
    r = client.post("/api/documents/upload", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 415 and r.json()["detail"]["code"] == "unsupported_type"
    r = client.post("/api/documents/upload", files={"file": ("fake.pdf", b"MZ not a pdf", "application/pdf")})
    assert r.status_code == 415 and r.json()["detail"]["code"] == "not_a_pdf"
    r = client.post("/api/documents/upload", files={"file": ("../../etc/passwd.pdf", b"", "application/pdf")})
    assert r.status_code in (415, 422)
    r = client.post("/api/documents/upload", files={"file": ("bad.pdf", b"%PDF-1.4 garbage", "application/pdf")})
    assert r.status_code == 201 and r.json()["document"]["status"] == "failed"


def test_upload_too_large(tmp_path, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    r = client.post("/api/documents/upload", files={"file": ("big.pdf", b"%PDF-1.4 " + b"0" * 100, "application/pdf")})
    assert r.status_code == 413


def test_path_traversal_ids_rejected():
    assert client.get("/api/documents/..%2F..%2Fetc").status_code in (404, 422)
    assert client.get("/api/documents/not-valid-id!").status_code == 422


def test_upload_is_idempotent_and_stores_generated_name(tmp_path):
    p = pdf(tmp_path, "a.pdf", V1)
    first, second = upload(p, "Old").json(), upload(p, "Older").json()
    assert second["deduplicated"] is True and first["document"]["id"] == second["document"]["id"]
    assert client.get("/api/documents").json()["total"] == 1
    from app.core.config import get_settings
    names = [f.name for f in get_settings().upload_dir.iterdir()]
    assert all("a.pdf" != n for n in names)


def test_scanned_upload_flagged_and_not_comparable(tmp_path):
    d = upload(make_scanned_pdf(tmp_path / "s.pdf")).json()["document"]
    assert d["status"] == "needs_ocr" and d["warnings"]
    ok = upload(pdf(tmp_path, "a.pdf", V1)).json()["document"]
    r = client.post("/api/comparisons", json={"doc_a_id": ok["id"], "doc_b_id": d["id"]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "document_not_ready"


def test_full_comparison_flow(tmp_path):
    cid, c, *_ = make_comparison(tmp_path)
    assert c["status"] == "completed", c
    counts = c["stats"]["counts"]
    assert counts["MODIFIED"] == 1 and counts["REMOVED"] == 1 and counts["ADDED"] == 1
    ch = client.get(f"/api/comparisons/{cid}/changes", params={"category": "MODIFIED"}).json()
    assert ch["total"] == 1 and "number_changed" in ch["items"][0]["flags"]
    item = ch["items"][0]
    assert item["old_page"] == 1 and item["old_section"] == "2.2" and item["match_method"] == "fuzzy"
    assert client.get(f"/api/comparisons/{cid}/changes", params={"q": "biometric"}).json()["total"] == 1
    assert client.get(f"/api/comparisons/{cid}/changes", params={"category": "BOGUS"}).status_code == 422
    page = client.get(f"/api/comparisons/{cid}/changes", params={"limit": 3, "offset": 2}).json()
    assert len(page["items"]) == 3 and page["offset"] == 2


def test_questions_extractive_and_insufficient_evidence(tmp_path):
    cid, *_ = make_comparison(tmp_path)
    r = client.post(f"/api/comparisons/{cid}/questions", json={"question": "Which clauses were removed?", "use_llm": False}).json()
    assert r["mode"] == "extractive" and "Video based verification" in r["answer"] and r["sufficient_evidence"]
    assert any(s["type"] == "change" and s["category"] == "REMOVED" for s in r["sources"])
    r = client.post(f"/api/comparisons/{cid}/questions", json={"question": "What is the airspeed velocity of an unladen swallow?", "use_llm": False}).json()
    assert r["sufficient_evidence"] is False and "insufficient evidence" in r["answer"].lower()
    r = client.post(f"/api/comparisons/{cid}/questions", json={"question": "Did the verification deadline days change?", "use_llm": True}).json()
    assert r["llm_error"] and r["mode"] == "extractive"  # graceful LLM fallback, never a paid API
    assert any("30 days" in (s.get("text") or (s.get("old") or {}).get("text") or "") for s in r["sources"])


def test_explain_without_llm_returns_503_with_hint(tmp_path):
    cid, *_ = make_comparison(tmp_path)
    ch = client.get(f"/api/comparisons/{cid}/changes", params={"category": "MODIFIED"}).json()["items"][0]
    r = client.post(f"/api/comparisons/{cid}/changes/{ch['id']}/explain")
    assert r.status_code == 503 and "Ollama" in r.json()["detail"]["message"]


@pytest.mark.parametrize("fmt,needle", [("md", b"# DocuDiff"), ("html", b"<!doctype html>"), ("pdf", b"%PDF")])
def test_export_formats(tmp_path, fmt, needle):
    cid, *_ = make_comparison(tmp_path)
    r = client.post(f"/api/comparisons/{cid}/export", json={"format": fmt}).json()
    body = client.get(r["download_url"])
    assert body.status_code == 200 and needle in body.content[:200]
    if fmt != "pdf":
        assert b"Comparison limitations" in body.content
        assert (b"<ins>15</ins>" if fmt == "html" else b"15 days") in body.content


def test_review_workflow(tmp_path):
    old = [("h1", "1. Rules"), ("p", "1.1 The institution shall verify the identity of every customer before onboarding them to any service offered.")]
    new = [("h1", "1. Rules"), ("p", "1.1 Before onboarding, each client must have their identity verified by the institution, using approved documents only.")]
    cid, c, *_ = make_comparison(tmp_path, old, new)
    pending = client.get(f"/api/comparisons/{cid}/changes", params={"category": "NEEDS_REVIEW"}).json()["items"]
    assert pending and pending[0]["review_status"] == "pending"
    r = client.post(f"/api/comparisons/{cid}/changes/{pending[0]['id']}/review", json={"decision": "reject"}).json()
    assert r["category"] == "REMOVED"
    cats = [x["category"] for x in client.get(f"/api/comparisons/{cid}/changes").json()["items"]]
    assert "ADDED" in cats and "NEEDS_REVIEW" not in cats
    assert client.post(f"/api/comparisons/{cid}/changes/{pending[0]['id']}/review", json={"decision": "confirm"}).status_code == 409


def test_delete_document_cascades(tmp_path):
    cid, c, da, db_ = make_comparison(tmp_path)
    exp = client.post(f"/api/comparisons/{cid}/export", json={"format": "md"}).json()
    r = client.delete(f"/api/documents/{da}").json()
    assert r["comparisons_deleted"] == 1
    assert client.get(f"/api/comparisons/{cid}").status_code == 404
    assert client.get(exp["download_url"]).status_code == 404
    assert client.get(f"/api/documents/{da}").status_code == 404
    from app.core.db import session_scope
    from app.models.tables import Chunk, Change
    with session_scope() as s:
        assert s.query(Change).count() == 0 and s.query(Chunk).filter(Chunk.document_id == da).count() == 0


def test_same_document_rejected_and_failed_retry(tmp_path):
    d = upload(pdf(tmp_path, "a.pdf", V1)).json()["document"]["id"]
    assert client.post("/api/comparisons", json={"doc_a_id": d, "doc_b_id": d}).status_code == 422
    assert client.post("/api/comparisons", json={"doc_a_id": d, "doc_b_id": "deadbeef"}).status_code == 404


def test_common_removals_requires_evidence_from_both_pairs(tmp_path):
    cid1, *_ = make_comparison(tmp_path)
    # Pair B: different documents that also drop the video-verification clause, plus an unrelated removal
    b1 = [("h1", "1. Intro"), ("p", "1.1 Video based verification is permitted only for resident customers."), ("p", "1.2 Archive records for five years in secure storage.")]
    b2 = [("h1", "1. Intro"), ("p", "1.2 Archive records for five years in secure storage.")]
    import pymupdf  # noqa
    cid2, c2, *_ = make_comparison(tmp_path / "..", b1, b2) if False else (None, None)
    da = upload(pdf(tmp_path, "b1.pdf", b1)).json()["document"]["id"]
    db_ = upload(pdf(tmp_path, "b2.pdf", b2)).json()["document"]["id"]
    cid2 = client.post("/api/comparisons", json={"doc_a_id": da, "doc_b_id": db_, "options": {"semantic": False}}).json()["id"]
    r = client.post("/api/common-removals", json={"comparison_a_id": cid1, "comparison_b_id": cid2}).json()
    assert r["confirmed"] == 1 and "Video based verification" in r["items"][0]["evidence_a"]["text"]
    assert r["items"][0]["evidence_b"]["comparison_id"] == cid2 and "Removed in BOTH" in r["direction"]
    assert client.post("/api/common-removals", json={"comparison_a_id": cid1, "comparison_b_id": cid1}).status_code == 422


def test_rate_limit(monkeypatch):
    from app.core.config import get_settings
    from app.core import limits
    monkeypatch.setattr(get_settings(), "rate_limit_read_per_minute", 3)  # GETs use the read bucket (see test_security.py)
    limits._hits.clear()
    codes = [client.get("/api/stats").status_code for _ in range(5)]
    assert codes[:3] == [200] * 3 and 429 in codes[3:]
    limits._hits.clear()
