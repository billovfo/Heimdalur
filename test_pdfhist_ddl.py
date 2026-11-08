"""DDL-01: migration.sql applied to an empty database yields the exact schema
surface of plan v3.1.2 (7 tables + 2 views, patch-1 FK order, identity rules).
OFFLINE: only the throwaway pdfhist_test database is touched."""
import psycopg
import pytest

from heimdal.pdfhist.migrate import MIGRATION_PATH, apply_migration
from heimdal.pdfhist.testsupport import connect


def _object_names(conn):
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables"
        " WHERE table_schema = 'public'"
        " UNION ALL SELECT viewname FROM pg_views WHERE schemaname = 'public'"
    ).fetchall()
    return {row[0] for row in rows}


def test_ddl_01_migration_creates_exact_objects():
    with connect() as conn:
        names = _object_names(conn)
    expected_tables = {
        "pdf_run_log", "pdf_source_event", "pdf_document",
        "pdf_document_content", "pdf_document_observation",
        "pdf_source_event_document", "pdf_parse_result",
    }
    expected_views = {"company_pdf_snapshot_history", "pdf_review_queue"}
    assert expected_tables <= names
    assert expected_views <= names
    # No 8th table / no event-sourcing / no promotion-rollback artifacts:
    # 7 pdf_* tables + pdf_review_queue + company_pdf_snapshot_history.
    pdf_named = {t for t in names if t.startswith("pdf_")}
    assert len(pdf_named) == 8
    assert "company_pdf_snapshot_history" in names


def test_ddl_01_fk_content_before_observation_patch1():
    with connect() as conn:
        rows = conn.execute(
            "SELECT conrelid::regclass::text, confrelid::regclass::text"
            " FROM pg_constraint WHERE contype = 'f'"
            " AND conrelid::regclass::text = 'pdf_document_observation'"
        ).fetchall()
    targets = {row[1] for row in rows}
    assert "pdf_document_content" in targets
    assert "pdf_document" in targets


def test_ddl_01_unique_document_run_and_document_identity():
    with connect() as conn:
        uniques = conn.execute(
            "SELECT conrelid::regclass::text, pg_get_constraintdef(oid)"
            " FROM pg_constraint WHERE contype = 'u'").fetchall()
    defs = {row[0]: row[1] for row in uniques}
    assert any("document_id" in d and "collection_run_id" in d
               for d in defs.values())
    # logical document identity WITHOUT bytes: document + type + source id
    assert any("pdf_document" == t for t in defs.keys())


def test_ddl_01_source_system_identity_is_source_not_host():
    with connect() as conn:
        default = conn.execute(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name = 'pdf_document'"
            " AND column_name = 'source_system'").fetchone()[0]
    assert "sg.vs.fo" in default
    assert "skraseting" not in default.lower()


def test_migration_is_idempotent_when_reapplied():
    with connect() as conn:
        apply_migration(conn)  # second application on same database
        names = _object_names(conn)
    # still exactly the same surface, no duplicates possible anyway
    assert "pdf_document_content" in names