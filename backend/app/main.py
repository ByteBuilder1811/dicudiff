import json
import logging
import time
from datetime import datetime, timedelta, timezone

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.routes import router
from app.core.config import get_settings
from app.core.db import init_engine, session_scope
from app.models.tables import Document
from app.services import pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("docudiff")


def purge_expired() -> int:
    days = get_settings().retention_days
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    with session_scope() as db:
        old = db.scalars(select(Document).where(Document.created_at < cutoff)).all()
        for d in old:
            pipeline.delete_document(db, d)
        return len(old)


def clean_stale_tmp(max_age_s: int = 3600) -> int:
    """Remove half-written upload temp files left by a crash (only files older than max_age_s)."""
    n = 0
    for f in get_settings().upload_dir.glob("tmp-*"):
        try:
            if f.is_file() and time.time() - f.stat().st_mtime > max_age_s:
                f.unlink()
                n += 1
        except OSError:
            pass
    return n


_SAFE = {"GET", "HEAD", "OPTIONS"}
_MB = 1024 * 1024


class _TooLarge(Exception):
    pass


class SecurityMiddleware:
    """Pure-ASGI hardening for a local, unauthenticated API (not a substitute for authentication):
    * Host allow-list (DNS-rebinding defence) and Origin check on state-changing requests (cross-site form POSTs);
    * request-body cap enforced while receiving, so an oversized upload is cut off instead of being spooled to disk first;
    * unexpected exceptions become a generic 500 and are logged by *type only* (Starlette would otherwise re-raise
      and uvicorn would print the full traceback);
    * conservative response headers."""

    def __init__(self, app, allowed_hosts: list[str], origins: list[str]):
        self.app, self.hosts, self.origins = app, {h.lower().strip("[]") for h in allowed_hosts}, set(origins)

    @staticmethod
    async def _send_json(send, status: int, code: str, message: str, headers=()):
        body = json.dumps({"detail": {"code": code, "message": message}}).encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *headers]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        hdr = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        method, path = scope["method"], scope["path"]
        if "*" not in self.hosts:
            host = (urlsplit("//" + hdr.get("host", "")).hostname or "").lower()
            if host not in self.hosts:
                return await self._send_json(send, 400, "bad_host", "Unrecognised Host header (see ALLOWED_HOSTS).")
        origin = hdr.get("origin")
        if origin is not None and method not in _SAFE and origin not in self.origins:
            if urlsplit(origin).netloc != hdr.get("host", ""):  # same-origin is fine
                return await self._send_json(send, 403, "bad_origin", "Cross-origin request blocked (see CORS_ORIGINS).")
        cfg = get_settings()
        cap = (cfg.max_upload_mb * _MB + 64 * 1024) if path == "/api/documents/upload" else _MB
        try:
            if int(hdr.get("content-length", "0") or 0) > cap:
                raise _TooLarge
        except ValueError:
            return await self._send_json(send, 400, "bad_request", "Invalid Content-Length.")
        except _TooLarge:
            return await self._send_json(send, 413, "too_large", "Request body is too large.")
        seen, exceeded = 0, [False]

        async def limited_receive():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > cap:
                    exceeded[0] = True
                    raise _TooLarge
            return msg

        started = False

        async def wrapped_send(msg):
            nonlocal started
            if exceeded[0]:  # FastAPI turns the aborted body read into a 400; report the real reason instead
                if msg["type"] == "http.response.start":
                    started = True
                    body = json.dumps({"detail": {"code": "too_large", "message": "Request body is too large."}}).encode()
                    await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                    await send({"type": "http.response.body", "body": body})
                return
            if msg["type"] == "http.response.start":
                started = True
                have = {k.lower() for k, _ in msg.get("headers", [])}
                extra = [(k, v) for k, v in ((b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"), (b"referrer-policy", b"no-referrer")) if k not in have]
                msg = {**msg, "headers": [*msg.get("headers", []), *extra]}
            await send(msg)

        try:
            await self.app(scope, limited_receive, wrapped_send)
        except _TooLarge:
            if not started:
                await self._send_json(send, 413, "too_large", "Request body is too large.")
        except Exception as exc:
            log.error("unhandled %s", type(exc).__name__)  # type only: never log document content
            if not started:
                await self._send_json(send, 500, "internal_error", "Unexpected server error.")


def create_app() -> FastAPI:
    cfg = get_settings()
    init_engine()
    clean_stale_tmp()
    stale = pipeline.recover_interrupted()
    if stale:
        log.warning("marked %d interrupted comparison(s) as failed (retryable)", stale)
    n = purge_expired()
    if n:
        log.info("retention: purged %d expired document(s)", n)
    app = FastAPI(title="DocuDiff API", version="1.0.0", description="Local-first, zero-cost document comparison and RAG.")
    origins = [o.strip() for o in cfg.cors_origins.split(",") if o.strip()]
    # Added first = inner; CORS (added last) is outermost so even 400/403/413 responses carry CORS headers for the UI.
    app.add_middleware(SecurityMiddleware, allowed_hosts=[h.strip() for h in cfg.allowed_hosts.split(",") if h.strip()], origins=origins)
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        msg = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
        return JSONResponse(status_code=422, content={"detail": {"code": "validation_error", "message": msg}})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.error("unhandled %s", type(exc).__name__)  # type only: never log document content
        return JSONResponse(status_code=500, content={"detail": {"code": "internal_error", "message": "Unexpected server error."}})

    return app


app = create_app()
