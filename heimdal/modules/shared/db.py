# heimdal/modules/shared/db.py
"""DB connection lifecycle: conn() + teardown + tx(). psycopg + Flask g.

Two primitives, one owner:
- conn(): request-scoped read connection (Flask g), committed/rolled back by
  the caller or request teardown. Prefer for single reads in request handlers.
  Fail-closed reads on it go through read_savepoint().
- tx(): canonical TRANSACTIONAL primitive for every multi-write operation.
  Single connection, dict rows, commit-on-success, rollback-on-error, close,
  loud failures. This is the ONLY place write transactions are constructed.
"""
import os
from contextlib import contextmanager

import psycopg
from flask import g
from psycopg.rows import dict_row

from heimdal.config import DB_URI


def conn():
    """Return the request-scoped DB connection, creating it on first access."""
    if "conn" not in g:
        g.conn = psycopg.connect(DB_URI, row_factory=dict_row)
    return g.conn


def _close(_exc):
    """Teardown: close the request-scoped connection if one was opened."""
    c = g.pop("conn", None)
    if c is not None:
        c.close()


@contextmanager
def read_savepoint(c=None):
    """Fail-closed read inside its own savepoint, always rolled back (F-8).

    Every fail-closed template global and analytics read runs in here. A
    failed statement, whether it raises or an inner reader swallows it, is
    undone with the savepoint, so it can never leave the request connection
    in InFailedSqlTransaction for the reads after it. Reads only: nothing
    done inside is kept.
    """
    c = conn() if c is None else c
    with c.transaction(force_rollback=True):
        yield c


def either_side(cols: str, view: str, a: str, b: str) -> str:
    """SQL for `a = %(id)s OR b = %(id)s` on a read view, as a UNION ALL (audit B-12).

    The OR form joins the whole view (7-15 ms per entity on 20k relations);
    each arm here is an index lookup (<0.5 ms). A row with a = b = id comes back
    once, as with OR. Execute with {"id": ...}; ORDER BY may be appended."""
    return (f"SELECT {cols} FROM {view} WHERE {a} = %(id)s UNION ALL"
            f" SELECT {cols} FROM {view} WHERE {b} = %(id)s AND {a} IS DISTINCT FROM %(id)s")


def write_dsn() -> str:
    """Env-derived write DSN. Fail-closed: no password-bearing defaults."""
    dsn = os.environ.get("HEIMDAL_PG_DSN", "")
    if not dsn:
        raise RuntimeError("HEIMDAL_PG_DSN is not set; refusing implicit DSN")
    return dsn


@contextmanager
def tx(dsn: str | None = None):
    """Canonical transaction: open(dict rows) → yield → commit/rollback → close.

    Contract (frozen P0.2a): commit on success, rollback on ANY error
    (BaseException so KeyboardInterrupt/SystemExit also roll back), close
    always, failures propagate loudly — never swallowed, never partial.
    """
    c = psycopg.connect(dsn or write_dsn(), row_factory=dict_row)
    try:
        yield c
    except BaseException:
        c.rollback()
        raise
    else:
        c.commit()
    finally:
        c.close()
