"""PDF_HISTORY feature flags - fail-closed runtime switches.

Task "ISOLERET DATABASE-LAG" sections 20/21/23/24/33/66/67:

* PDF_HISTORY_INTEGRATION_ENABLED - when false (default), no registry_projection
  data may influence existing Heimdal behavior, and no consumer may select
  PDF-derived state as authoritative.
* PDF_HISTORY_UI_ENABLED - when false (default), no registry_projection data is
  exposed in the UI. UI exposure additionally REQUIRES integration to be
  enabled: ui_enabled=true + integration_enabled=false must still expose
  nothing (fail-closed section 24).
* PDF_HISTORY_API_ENABLED - intentionally NOT implemented: the current Heimdal
  app has no separate public API surface, so a third flag would be an
  unnecessary flag (task section 22). Revisit only when a real API surface
  exists.

Authoritative source of truth for runtime flags: the environment (heimdal
config convention, heimdal/config.py). registry_projection.projection_setting
is the DB mirror that admin CLI commands write; it is consulted ONLY when the
corresponding environment variable is unset. A deployment that pins the
environment variable always overrides the DB mirror (documented precedence,
single effective resolution).

Invalid/unparseable values resolve to False (fail closed).
"""
from __future__ import annotations

import os

import psycopg

_ENV_INTEGRATION = "PDF_HISTORY_INTEGRATION_ENABLED"
_ENV_UI = "PDF_HISTORY_UI_ENABLED"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _parse_env(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    token = raw.strip().lower()
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    return False  # unparseable -> fail closed


# F-13: the portal memoises flag resolution per request. create_app installs a
# hook that returns one dict per request (flask.g); with no hook (CLIs, tests
# outside a request) every call resolves afresh, and pdfhist stays Flask-free.
_memo_hook = lambda: None  # noqa: E731


def set_memo_hook(hook) -> None:
    global _memo_hook
    _memo_hook = hook or (lambda: None)


def memo(key: str, resolve):
    """resolve() once per request (when a hook is installed), else every call."""
    cache = _memo_hook()
    if cache is None:
        return resolve()
    if key not in cache:
        cache[key] = resolve()
    return cache[key]


def _setting(conn: psycopg.Connection | None, key: str) -> bool:
    if conn is None:
        return False
    return memo(f"setting:{key}", lambda: _read_setting(conn, key))


def _read_setting(conn: psycopg.Connection, key: str) -> bool:
    try:
        row = conn.execute(
            "SELECT value_json FROM registry_projection.projection_setting "
            "WHERE key = %s", (key,)).fetchone()
    except psycopg.errors.UndefinedTable:
        return False
    if row is None:
        return False
    return row[0] is True


def integration_enabled(conn: psycopg.Connection | None = None) -> bool:
    """True only if the kill switch is explicitly enabled."""
    env = _parse_env(_ENV_INTEGRATION)
    if env is not None:
        return env
    return bool(_setting(conn, "integration_enabled"))


def ui_enabled(conn: psycopg.Connection | None = None) -> bool:
    """The UI switch value (independent of the integration switch)."""
    env = _parse_env(_ENV_UI)
    if env is not None:
        return env
    return bool(_setting(conn, "ui_enabled"))


def ui_exposure_allowed(conn: psycopg.Connection | None = None) -> bool:
    """UI may expose projection data only when BOTH switches are on."""
    return integration_enabled(conn) and ui_enabled(conn)