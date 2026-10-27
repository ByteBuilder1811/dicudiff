"""Ollama failure handling against a fake local HTTP server (no real model, no network beyond 127.0.0.1)."""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.db import session_scope
from app.main import app
from app.models.tables import Comparison
from app.services import local_llm, rag
from tests.test_api import make_comparison
from tests.test_rag import ask, make_comp

client = TestClient(app)


class Fake:
    """Configurable fake Ollama. behaviour: dict(tags=..., chat=...) with status/body/delay."""

    def __init__(self):
        self.tags_calls = self.chat_calls = 0
        self.models = ["llama3.2:3b"]
        self.chat = {"status": 200, "body": {"message": {"content": "ok [E1]"}}, "delay": 0.0, "raw": None}
        self.tags = {"status": 200, "raw": None, "delay": 0.0}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body, raw=None):
                data = raw if raw is not None else json.dumps(body).encode()
                try:
                    self.send_response(status); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
                except OSError:
                    pass

            def do_GET(self):
                outer.tags_calls += 1
                time.sleep(outer.tags["delay"])
                self._send(outer.tags["status"], {"models": [{"name": m} for m in outer.models]}, outer.tags["raw"])

            def do_POST(self):
                outer.chat_calls += 1
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                time.sleep(outer.chat["delay"])
                self._send(outer.chat["status"], outer.chat["body"], outer.chat["raw"])

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def reply(self, content):
        self.chat["body"] = {"message": {"content": content}}


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    s = get_settings()
    monkeypatch.setattr(s, "ollama_url", f.url)
    monkeypatch.setattr(s, "ollama_model", "llama3.2:3b")
    monkeypatch.setattr(s, "ollama_timeout_s", 2)
    local_llm.clear_status_cache()
    yield f
    f.srv.shutdown(); f.srv.server_close()
    local_llm.clear_status_cache()


def timed(fn, *a, **k):
    t = time.monotonic()
    try:
        return fn(*a, **k), time.monotonic() - t
    except Exception as exc:  # noqa: BLE001
        return exc, time.monotonic() - t


MSG = [{"role": "user", "content": "hi"}]


# ------------------------------------------------------------------ individual failure modes
def test_unreachable(monkeypatch):
    monkeypatch.setattr(get_settings(), "ollama_url", "http://127.0.0.1:9")
    local_llm.clear_status_cache()
    st = local_llm.status()
    assert st["reachable"] is False and "ollama serve" in st["hint"]
    exc, dt = timed(local_llm.chat, MSG)
    assert isinstance(exc, local_llm.LLMUnavailable) and "ollama serve" in str(exc) and dt < 6


def test_model_not_installed(fake):
    fake.models = ["qwen2.5-coder:7b"]
    st = local_llm.status()
    assert st["reachable"] and not st["model_installed"] and "ollama pull llama3.2:3b" in st["hint"]
    exc, _ = timed(local_llm.chat, MSG)
    assert isinstance(exc, local_llm.LLMUnavailable) and "not installed" in str(exc) and fake.chat_calls == 0


def test_bare_model_name_matches_latest_tag(fake, monkeypatch):
    fake.models = ["mymodel:latest"]
    monkeypatch.setattr(get_settings(), "ollama_model", "mymodel")
    assert local_llm.status()["model_installed"] is True


def test_chat_timeout_is_bounded_and_clear(fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "ollama_timeout_s", 1)
    fake.chat["delay"] = 4
    exc, dt = timed(local_llm.chat, MSG)
    assert isinstance(exc, local_llm.LLMUnavailable) and "within 1s" in str(exc) and dt < 4, (exc, dt)


def test_status_probe_timeout_is_bounded(fake):
    fake.tags["delay"] = 6
    st, dt = timed(local_llm.status)
    assert st["reachable"] is False and 2.0 <= dt < 5, dt  # 2.5 s cap per probe


@pytest.mark.parametrize("status,needle", [(500, "HTTP 500"), (404, "does not have model"), (503, "HTTP 503")])
def test_http_errors(fake, status, needle):
    fake.chat.update(status=status, body={"error": "boom"})
    exc, _ = timed(local_llm.chat, MSG)
    assert isinstance(exc, local_llm.LLMUnavailable) and needle in str(exc)
    assert "boom" not in str(exc)


@pytest.mark.parametrize("raw", [b"<html>nginx</html>", b"{}", b'{"message": {}}', b'{"message": {"content": "  "}}', b'{"message": {"content": 5}}'])
def test_malformed_or_empty_chat_body(fake, raw):
    fake.chat["raw"] = raw
    exc, _ = timed(local_llm.chat, MSG)
    assert isinstance(exc, local_llm.LLMUnavailable) and str(exc)


def test_tags_non_json_means_unreachable_not_crash(fake):
    fake.tags["raw"] = b"not json"
    st = local_llm.status()
    assert st["reachable"] is False and st["hint"]


# ------------------------------------------------------------------ status() cost
def test_one_chat_probes_status_once_and_repeat_chats_reuse_it(fake):
    for _ in range(3):
        assert local_llm.chat(MSG) == "ok [E1]"
    assert fake.tags_calls == 1 and fake.chat_calls == 3


def test_failure_clears_status_cache_so_recovery_is_noticed(fake):
    assert local_llm.chat(MSG)
    fake.chat.update(status=500)
    with pytest.raises(local_llm.LLMUnavailable):
        local_llm.chat(MSG)
    fake.chat.update(status=200)
    assert local_llm.chat(MSG) and fake.tags_calls == 2


def test_health_status_is_always_fresh(fake):
    local_llm.status(); local_llm.status()
    assert fake.tags_calls == 2


# ------------------------------------------------------------------ explain_change
@pytest.mark.parametrize("content,ok", [
    ('{"observed_change": "30 -> 15 days [E1][E2]", "possible_implications": "", "needs_human_review": false, "caveats": ""}', True),
    ('Sure! Here you go: {"observed_change": "x", "possible_implications": "", "needs_human_review": true, "caveats": ""} hope it helps', True),
    ("I cannot produce JSON, the clause changed.", False),
    ('{"observed_change": "broken", ', False),
    ("Result: {not valid json at all}", False),
    ('["a", "b"]', False),
    ('{"observed_change": {"nested": 1}, "needs_human_review": "false"}', True),
])
def test_explain_change_never_raises_on_model_output(fake, content, ok):
    fake.reply(content)
    out = local_llm.explain_change("MODIFIED", "30 days", "15 days", ["number_changed"])
    assert set(out) >= {"observed_change", "possible_implications", "needs_human_review", "caveats", "model", "generated_by"}
    assert out["generated_by"] == "ai" and all(isinstance(out[k], str) for k in ("observed_change", "possible_implications", "caveats"))
    if not ok:
        assert out["needs_human_review"] is True and "json" in out["caveats"].lower()


def test_explain_string_false_is_false(fake):
    fake.reply('{"observed_change": "x", "needs_human_review": "false"}')
    assert local_llm.explain_change("MODIFIED", "a", "b", [])["needs_human_review"] is False


# ------------------------------------------------------------------ rag fallback + API
def test_rag_falls_back_to_extractive_for_every_llm_failure(fake, tmp_path):
    cid, *_ = make_comp(tmp_path)
    q = "Did the verification deadline days change?"
    fake.chat.update(status=500)
    r = ask(cid, q)
    assert r["mode"] == "extractive" and "HTTP 500" in r["llm_error"] and "30 days" in r["answer"] and "[E1]" in r["answer"]
    fake.chat.update(status=200, delay=3); get_settings().ollama_timeout_s = 1
    r, dt = timed(ask, cid, q)
    assert r["mode"] == "extractive" and "within 1s" in r["llm_error"] and dt < 6
    fake.chat["delay"] = 0; fake.models = []
    local_llm.clear_status_cache()
    r = ask(cid, q)
    assert r["mode"] == "extractive" and "not installed" in r["llm_error"]


def test_rag_with_healthy_fake_llm_runs_one_chat_and_one_probe(fake, tmp_path):
    cid, *_ = make_comp(tmp_path)
    fake.reply("The deadline changed from 30 to 15 days [E1].")
    r = ask(cid, "Did the verification deadline days change?")
    assert r["mode"] == "llm" and r["citation_warning"] is None and fake.chat_calls == 1 and fake.tags_calls == 1


def test_explain_endpoint_503_with_hint_for_each_failure(fake, tmp_path):
    cid, *_ = make_comparison(tmp_path)
    ch = client.get(f"/api/comparisons/{cid}/changes", params={"category": "MODIFIED"}).json()["items"][0]
    url = f"/api/comparisons/{cid}/changes/{ch['id']}/explain"
    fake.models = []
    r = client.post(url)
    assert r.status_code == 503 and "ollama pull" in r.json()["detail"]["message"] and r.json()["detail"]["code"] == "llm_unavailable"
    fake.models = ["llama3.2:3b"]; fake.chat.update(status=500); local_llm.clear_status_cache()
    assert client.post(url).status_code == 503
    fake.chat.update(status=200); fake.reply("plain text, no json")
    r = client.post(url)  # non-JSON output must be a 200 with a caveat, not a 500
    assert r.status_code == 200 and r.json()["ai_explanation"]["needs_human_review"] is True
    fake.reply('{"observed_change": "30 -> 15 days", "possible_implications": "", "needs_human_review": false, "caveats": ""}')
    r = client.post(url)
    assert r.status_code == 200 and r.json()["ai_explanation"]["observed_change"].startswith("30")


def test_explain_endpoint_unreachable_ollama(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "ollama_url", "http://127.0.0.1:9")
    local_llm.clear_status_cache()
    cid, *_ = make_comparison(tmp_path)
    ch = client.get(f"/api/comparisons/{cid}/changes", params={"category": "MODIFIED"}).json()["items"][0]
    t = time.monotonic()
    r = client.post(f"/api/comparisons/{cid}/changes/{ch['id']}/explain")
    assert r.status_code == 503 and "Ollama" in r.json()["detail"]["message"] and time.monotonic() - t < 4


# ------------------------------------------------------------------ no hosted API anywhere
def test_only_loopback_http_is_ever_used(fake, tmp_path, monkeypatch):
    seen = []
    real = httpx.Client.send

    def spy(self, request, **kw):
        seen.append(request.url.host)
        return real(self, request, **kw)

    monkeypatch.setattr(httpx.Client, "send", spy)
    cid, *_ = make_comp(tmp_path)
    fake.reply("x [E1]")
    ask(cid, "Did the verification deadline days change?")
    assert seen and set(seen) == {"127.0.0.1"}


def test_source_has_no_hosted_api_references():
    root = Path(__file__).resolve().parents[1] / "app"
    banned = re.compile(r"openai|api\.cohere|generativelanguage|huggingface\.co/api|replicate\.com|together\.ai|groq", re.I)
    offenders = [str(p.relative_to(root)) for p in root.rglob("*.py") if banned.search(p.read_text())]
    assert offenders == []
    urls = set(re.findall(r"https?://[^\s\"')]+", (root / "services" / "local_llm.py").read_text()))
    assert all("localhost" in u or "{" in u for u in urls)
