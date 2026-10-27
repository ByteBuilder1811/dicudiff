"""Tiny in-memory sliding-window rate limiter (single-process local deployment).

Two buckets per client address: cheap reads (GET/HEAD) get a high ceiling so the UI's status polling
(Dashboard 2 req/1.5 s, Workspace 1 req/s) never trips it; everything else (uploads, comparisons, exports,
LLM calls, deletes) uses the strict `rate_limit_per_minute`. The key is the TCP peer address; X-Forwarded-For is
deliberately NOT trusted (it is client-controlled). Behind the Vite dev proxy or nginx every client is the proxy's
address, so this is a global limit there, which is acceptable for a single-user tool.
"""
import hmac
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.core.config import get_settings

_hits: dict[tuple[str, str], deque] = defaultdict(deque)
_lock = threading.Lock()


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def rate_limit(request: Request) -> None:
    cfg = get_settings()
    read = request.method in ("GET", "HEAD")
    limit = cfg.rate_limit_read_per_minute if read else cfg.rate_limit_per_minute
    key = (request.client.host if request.client else "local", "read" if read else "write")
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            raise api_error(429, "rate_limited", f"Too many requests (limit {limit}/min). Please slow down.")
        q.append(now)


def require_token(request: Request) -> None:
    """Optional shared-secret guard (DOCUDIFF_API_TOKEN). Off when empty. /api/health stays open for liveness probes.
    Minimal protection for network exposure: not user authentication, no TLS, no per-user audit."""
    token = get_settings().docudiff_api_token
    if not token or request.url.path == "/api/health":
        return
    auth = request.headers.get("authorization", "")
    scheme, _, supplied = auth.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.strip().encode(), token.encode()):
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Missing or invalid API token."},
                            headers={"WWW-Authenticate": "Bearer"})
