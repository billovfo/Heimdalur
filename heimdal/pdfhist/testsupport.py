"""Test support for the OFFLINE pdfhist suite.

* Creates/drops ONE dedicated throwaway PostgreSQL database (pdfhist_test) and
  applies the migration to it. Never touches the heimdal/QA database.
* No root conftest.py: plain import of this module per test file keeps the
  existing flat-repo test suite untouched.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import psycopg

from .db import admin_dsn, qa_dsn, test_db_name
from .migrate import bootstrap_test_database

# The fixtures are original register PDFs (personal data), so they live in the
# evidence store outside git (audit 2026-11 I-5, retention rule).
FIXTURE_DIR = Path(os.environ.get("HEIMDAL_FIXTURES_DIR") or Path.home().joinpath(
    ".local/share/heimdal/evidence/2026-11-audit/fixtures/pdfhist"))

_state: dict = {}


def fixture_path(name: str) -> Path:
    path = FIXTURE_DIR / name
    if not path.is_file():
        import pytest
        pytest.skip(f"real-PDF fixture {name} not in HEIMDAL_FIXTURES_DIR={FIXTURE_DIR} "
                    "(kept outside git: audit 2026-11 I-5)", allow_module_level=True)
    return path


def pii_digest(value) -> str:
    """16-hex sha256 of a person name/address, or of a list/tuple/set of them:
    tests pin exact register values without holding them in git (I-5)."""
    if isinstance(value, (set, frozenset)):
        value = sorted(value)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()[:16]


def fixture_bytes(name: str) -> bytes:
    return fixture_path(name).read_bytes()


def fixture_text(name: str) -> str:
    return fixture_path(name).read_text(encoding="utf-8")


def ensure_test_database() -> str:
    """Drop + re-create + migrate the dedicated test database (once per process).

    Audit B-14: the evidence migration, then the same ledger as production
    (projection, review, review-immutability, schema_migrations), so tests run
    against the production shape, never against a test double's looser one."""
    if "dsn" not in _state:
        dsn = bootstrap_test_database(admin_dsn(), test_db_name())
        with psycopg.connect(dsn) as conn:
            from .migrate import apply_migration
            from . import migrations
            apply_migration(conn)
            migrations.apply(conn)
        _state["dsn"] = dsn
    return _state["dsn"]


def connect() -> psycopg.Connection:
    return psycopg.connect(ensure_test_database())


def ensure_source_event(conn, event_id: int) -> int:
    """A pdf_source_event with this id: the FK target of company_person_role
    seeds whose id is part of a case key. Moves the id sequence past it."""
    conn.execute(
        "INSERT INTO pdf_source_event (id, company_id, requested_regnr, row_skrasnr,"
        " source_row_key, raw_text, page_no, row_ordinal) VALUES (%s, %s, %s, %s, %s,"
        " 'test event', 0, 1) ON CONFLICT DO NOTHING",
        (event_id, event_id, str(event_id), str(event_id), f"test-event-{event_id}"))
    conn.execute("SELECT setval(pg_get_serial_sequence('pdf_source_event', 'id'),"
                 " (SELECT max(id) FROM pdf_source_event))")
    return event_id


def source_event_dict(company_id: int = 10, row_skrasnr: str = "10",
                      source_row_key: str | None = None,
                      source_event_date: str | None = None,
                      raw_text: str | None = None) -> dict:
    """Build a pdf_source_event row descriptor (verified row from search)."""
    return {
        "company_id": company_id,
        "row_skrasnr": row_skrasnr,
        "source_row_key": source_row_key or f"sg.vs.fo#{row_skrasnr}",
        "source_event_date": source_event_date,
        "raw_text": raw_text or "row text",
        "page_no": 0,
        "row_ordinal": 1,
    }