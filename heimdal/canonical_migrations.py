"""DDL ledger for the canonical heimdal DB (audit B-17).

sql/migrations/NNNN_*.sql are the canonical DB's schema history, recorded with
checksums in its own schema_migrations, under the pdfhist ledger's contract
(heimdal/pdfhist/migrations.py): applied history is immutable, a fix is a NEW
file, a changed file is a hard failure. Each file runs whole, in the one
transaction of `--apply`.

0001_baseline is the post-cutover schema of the 2026-09-04 dump. A DB that
already holds it and has no ledger rows (a restore of that dump or of the dev
DB) adopts it without running it; an empty DB runs it. Everything after 0001 is
idempotent, so the dev DB, which got 0002-0006 by hand, adopts them as no-ops.
The root schema*.sql files are the pre-cutover history: they still build a
fresh pre-cutover DB (`heimdal_collect.py init`) and refuse a post-cutover one.

  python3 -m heimdal.canonical_migrations --status | --apply  [--dsn DSN]   (default $HEIMDAL_DB)
"""
from __future__ import annotations

from pathlib import Path

from psycopg.rows import tuple_row

from heimdal.pdfhist import migrations as ledger

DIR = Path(__file__).resolve().parent.parent / "sql" / "migrations"
FILES: tuple[tuple[str, Path], ...] = tuple(
    (p.stem, p) for p in sorted(DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")))


def _one(conn, sql: str):
    return conn.cursor(row_factory=tuple_row).execute(sql).fetchone()[0]


def status(conn) -> dict:
    return ledger.status(conn, FILES)


# 0007 (the heimdal_reader role: its grants, CONNECT and per-database
# read-only default) is re-run on every --apply. A restore records 0007 as
# applied but loses its effect: pg_dump carries neither the database-level
# settings (cluster catalogs, keyed by DB oid) nor, with --no-acl, the grants.
# The file is idempotent (REVOKE ALL, then GRANT), so re-running it is exact.
REASSERT = "0007_heimdal_reader"


def apply(conn) -> list[str]:
    """Adopt 0001 where the DB already holds it, apply every pending file, then
    re-run 0007 (REASSERT), whose effect a restore loses."""
    ledger.ensure_ledger(conn)
    if (_one(conn, "SELECT count(*) FROM schema_migrations") == 0
            and _one(conn, "SELECT to_regclass('registry_ownership.build_meta') IS NOT NULL")):
        ledger.baseline(conn, FILES[:1])
    applied = ledger.apply(conn, FILES, run=lambda c, sql: c.execute(sql))
    if REASSERT not in applied:
        conn.execute(dict(FILES)[REASSERT].read_text())
    return applied


def main(argv=None) -> int:
    import argparse
    import os

    import psycopg
    ap = argparse.ArgumentParser(prog="canonical_migrations")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--dsn", default=os.environ.get("HEIMDAL_DB", ""))
    args = ap.parse_args(argv)
    if not args.dsn:
        raise SystemExit("HEIMDAL_DB or --dsn required (fail-closed)")
    conn = psycopg.connect(args.dsn)
    try:
        if args.apply:
            print("applied:", apply(conn))
            conn.commit()
        st = status(conn)
        for name, (state, detail) in st.items():
            print(f"{name}: {state} ({detail})")
        return 0 if all(s == "APPLIED" for s, _ in st.values()) else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
