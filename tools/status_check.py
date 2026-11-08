"""Live status probe (P2.3/G7). Read-only. Prints facts JSON.

STATUS.md snapshots these values; a mismatch between the page and this
output means the page is STALE.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _dsn(name: str) -> str:
    dsn = os.environ.get(name, "")
    if not dsn:
        raise RuntimeError(f"{name} not set")
    return dsn


def main() -> int:
    import subprocess

    import psycopg
    from psycopg.rows import dict_row
    out: dict = {}
    try:
        proj = psycopg.connect(_dsn("HEIMDAL_PDFHIST_PROJECTION_DSN"),
                               row_factory=dict_row)
        try:
            run = proj.execute(
                "SELECT projection_run_id, content_hash, status FROM "
                "registry_projection.projection_run WHERE status='ACTIVE' "
                "ORDER BY 1 DESC LIMIT 1").fetchone()
            out["projection"] = dict(run) if run else None
        except psycopg.Error as e:  # reachable, but no projection schema
            proj.rollback()
            out["projection"] = f"UNAVAILABLE: {e}"
        try:
            el = proj.execute(
                "SELECT count(*) c, count(*) FILTER (WHERE link_status='LINKED') l "
                "FROM registry_projection.entity_link "
                "WHERE entity_type='PERSON'").fetchone()
            out["person_links"] = dict(el) if isinstance(el, dict) \
                else {"c": el[0], "l": el[1]}
        except Exception as e:
            out["person_links"] = f"UNAVAILABLE: {e}"
        try:
            dec = proj.execute(
                "SELECT count(*) c FROM "
                "registry_projection.person_review_decision").fetchone()
            out["review_decisions"] = dec["c"] if isinstance(dec, dict) else dec[0]
        except Exception as e:
            out["review_decisions"] = f"UNAVAILABLE: {e}"
        try:
            out["ledger"] = [dict(r) if isinstance(r, dict) else list(r)
                             for r in proj.execute(
                                 "SELECT name, applied_at FROM schema_migrations"
                                 " ORDER BY name").fetchall()]
        except Exception as e:
            out["ledger"] = f"UNAVAILABLE: {e}"
        proj.close()
    except Exception as e:
        out["projection_db"] = f"UNREACHABLE: {e}"
    try:
        canon = psycopg.connect(_dsn("HEIMDAL_READ_DB"))
        n = canon.execute("SELECT count(*) FROM canonical_entity").fetchone()
        out["canonical_entities"] = n["count"] if isinstance(n, dict) else n[0]
        canon.close()
    except Exception as e:
        out["canonical_db"] = f"UNREACHABLE: {e}"
    # B-1/B-2 (audit 2026-11): a DB that only counts canonical_entity can still
    # be portal-dead. check_b asserts every relation the code reads exists.
    chk = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(__file__), "audit", "check_b.py"),
         os.environ.get("HEIMDAL_DB") or os.environ.get("HEIMDAL_READ_DB", "")],  # owner DSN (B-9)
        capture_output=True, text=True)
    out["required_relations"] = "OK" if chk.returncode == 0 else (
        (chk.stdout + chk.stderr).strip().splitlines() or ["FAIL"])[-1]
    try:
        latest = json.load(open("backups/LATEST.json"))
        out["backups"] = latest
    except Exception as e:
        out["backups"] = f"UNAVAILABLE: {e}"
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
