# heimdal/modules/shared/web.py
"""HTTP/web concerns: network boundary, rate limiting + security headers."""
import ipaddress
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from flask import Response, abort, current_app, request

from heimdal.config import _RATE_EXCEEDED_TEXT

# ponytail: global in-process dict + lock; reset on restart. Enough for a
# 127.0.0.1-only read-only tool — a shared or multi-process deployment would
# need a real shared store (nginx limit_req, Redis) instead.
_requests = {}
_requests_lock = threading.Lock()
_last_sweep = [0.0]  # monotonic time of the last stale-key sweep


def _local_only():
    """before_request, registered first (I-6, F-7, E-6): loopback clients only,
    and a state-changing request must not come from a foreign page. The Host
    header itself is checked by TRUSTED_HOSTS (400). No Origin and no Referer
    (curl, test client) passes. Remove the loopback check when auth lands."""
    try:
        loopback = ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        abort(403)
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if source is None:
        return None
    u = urlsplit(source)  # "null" (opaque origin) has no hostname -> foreign
    if u.scheme not in ("http", "https") or u.hostname not in current_app.config["TRUSTED_HOSTS"]:
        abort(403)
    return None


def _rate_limit():
    """before_request: sliding window per client IP, static files skipped."""
    if request.endpoint == "static":
        return None
    cfg = current_app.config
    if cfg.get("TESTING"):
        return None
    limit, window = cfg["RATE_LIMIT"], cfg["RATE_WINDOW"]
    now = time.monotonic()
    ip = request.remote_addr or "unknown"
    with _requests_lock:
        # F-12: once per window, drop every IP whose newest hit has expired, so
        # _requests holds at most the IPs of the last two windows.
        if now - _last_sweep[0] >= window:
            for k in [k for k, d in _requests.items() if not d or now - d[-1] >= window]:
                del _requests[k]
            _last_sweep[0] = now
        seen = _requests.setdefault(ip, deque())
        while seen and now - seen[0] >= window:
            seen.popleft()
        if len(seen) >= limit:
            resp = Response(_RATE_EXCEEDED_TEXT, status=429)
            resp.headers["Retry-After"] = str(window)
            return resp
        seen.append(now)
    return None


def _security_headers(resp):
    """after_request: lavthængende hærdning — headere er gratis."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    # same-origin, not no-referrer: with no-referrer a browser sends
    # "Origin: null" on our own form POSTs, which _local_only must refuse.
    # Cross-origin requests still get no Referer at all.
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault(
        "Content-Security-Policy",
        # Scripts: 'self' only (G-05). Templates have no inline <script> or on*=
        # handlers, data reaches JS via data-* or <script type="application/json">.
        # Styles keep 'unsafe-inline' for the style="" attributes.
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self';"
        " img-src 'self' data: https://tile.openstreetmap.org; frame-ancestors 'none'; base-uri 'none';"
        " form-action 'self'")
    return resp
