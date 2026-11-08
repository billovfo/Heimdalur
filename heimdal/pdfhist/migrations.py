"""Immutable migration ledger (P1.2). Plain SQL files + one ledger table.

Contract (frozen):
- Applied history is immutable. Checksum mismatch on a recorded migration is
  always a hard failure. Fixes ship as NEW migration files. No re-run-changed
  path exists in normal operation.
- `baseline` is stateless adoption for pre-ledger databases: it ensures the
  ledger table exists itself, then proceeds ONLY when the ledger is empty,
  recording current checksums without executing. With >=1 row it always
  refuses. No baseline_used flag, no DB-identity tracking.
- Scope: the ordered registry_projection-layer files (projection, review,
  review-immutability). Base evidence migration (migrate.py) is out of scope by design.
- The canonical heimdal DB has its own file list on the same ledger
  (heimdal/canonical_migrations.py, audit B-17): every function takes `files`.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

PDFHIST_DIR = Path(__file__).resolve().parent

MIGRATION_FILES: tuple[tuple[str, Path], ...] = (
    ("projection", PDFHIST_DIR / "projection_migration.sql"),
    ("review", PDFHIST_DIR / "review_migration.sql"),
    ("review-immutability", PDFHIST_DIR / "review_immutability_migration.sql"),
)

LEDGER_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
    "checksum TEXT NOT NULL)"
)


def checksum_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _recorded(conn, name: str):
    row = conn.execute(
        "SELECT checksum FROM schema_migrations WHERE name = %s",
        (name,)).fetchone()
    if row is None:
        return None
    return row["checksum"] if isinstance(row, dict) else row[0]


def ensure_ledger(conn) -> None:
    conn.execute(LEDGER_DDL)


def status(conn, files=None) -> dict:
    """{name: (state, detail)} state in APPLIED/CLEAN, MISMATCH, PENDING."""
    ensure_ledger(conn)
    out = {}
    for name, path in files or MIGRATION_FILES:
        rec = _recorded(conn, name)
        if rec is None:
            out[name] = ("PENDING", "not recorded")
        elif rec == checksum_of(path):
            out[name] = ("APPLIED", "checksum match")
        else:
            out[name] = ("MISMATCH", "recorded checksum differs: hard failure")
    return out


def _run_split(conn, sql: str) -> None:
    from heimdal.pdfhist.migrate import split_statements
    for statement in split_statements(sql):
        conn.execute(statement)


def apply(conn, files=None, run=_run_split) -> list[str]:
    """Apply pending migrations in order. MISMATCH always raises.
    `run(conn, sql)` executes one file (default: statement by statement)."""
    ensure_ledger(conn)
    done = []
    for name, path in files or MIGRATION_FILES:
        rec = _recorded(conn, name)
        digest = checksum_of(path)
        if rec is not None:
            if rec != digest:
                raise RuntimeError(
                    "migration %r changed after apply (checksum mismatch); "
                    "ship a NEW migration instead" % name)
            continue
        run(conn, path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_migrations (name, checksum) VALUES (%s, %s)",
            (name, digest))
        done.append(name)
    return done


def baseline(conn, files=None) -> list[str]:
    """Stateless adoption: record current checksums WITHOUT executing.

    Proceeds only when the ledger exists and is EMPTY. Refuses (raises) with
    >=1 row. No flags, no identity tracking.
    """
    ensure_ledger(conn)
    count = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()
    n = count["count"] if isinstance(count, dict) else count[0]
    if n > 0:
        raise RuntimeError(
            "baseline refused: ledger already holds %d row(s); history is "
            "immutable" % n)
    done = []
    for name, path in files or MIGRATION_FILES:
        conn.execute(
            "INSERT INTO schema_migrations (name, checksum) VALUES (%s, %s)",
            (name, checksum_of(path)))
        done.append(name)
    return done


def main(argv=None) -> int:
    import argparse
    import os
    import psycopg
    ap = argparse.ArgumentParser(prog="migrations")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--dsn", default=os.environ.get("HEIMDAL_PG_DSN", ""))
    args = ap.parse_args(argv)
    if not args.dsn:
        raise SystemExit("HEIMDAL_PG_DSN or --dsn required (fail-closed)")
    conn = psycopg.connect(args.dsn)
    try:
        if args.status:
            for name, (state, detail) in status(conn).items():
                print(f"{name}: {state} ({detail})")
            return 0
        if args.baseline:
            print("baselined:", baseline(conn))
            conn.commit()
            return 0
        if args.apply:
            print("applied:", apply(conn))
            conn.commit()
            return 0
        ap.print_help()
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
