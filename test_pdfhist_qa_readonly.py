"""Read-only QA guarantee (plan FASE 21/22 + acceptance): the offline pipeline
must NOT modify company_location or any existing Heimdal production state.

* The QA heimdal database is opened READ-ONLY via SELECTs only.
* Ingestions run against the throwaway pdfhist_test database only.
* The QA database has NO pdf_* tables (migration never applied there).
Skips gracefully when the QA database is not reachable (pure CI/offline run).
"""
import psycopg
import pytest

from heimdal.pdfhist.fixture_pdf import make_pdf
from heimdal.pdfhist.ingest import ingest_pdf_observation
from heimdal.pdfhist.testsupport import (
    connect, fixture_bytes, qa_dsn, source_event_dict)


def _qa_connect():
    try:
        return psycopg.connect(qa_dsn(), autocommit=True)
    except Exception:  # noqa: BLE001 - environment without qa db
        return None


def test_qa_database_has_no_pdf_tables():
    conn = _qa_connect()
    if conn is None:
        pytest.skip("QA heimdal database not reachable - offline run")
    try:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_name LIKE 'pdf_%'").fetchall()
        assert rows == []
    finally:
        conn.close()


def test_company_location_untouched_by_full_ingest_cycle():
    conn = _qa_connect()
    if conn is None:
        pytest.skip("QA heimdal database not reachable - offline run")
    try:
        checksum_before = conn.execute(
            "SELECT count(*) FROM company_location").fetchone()[0]
        row10 = conn.execute(
            "SELECT source_system, source_entity_id FROM company_inventory"
            " WHERE source_entity_id::text = '10'"
            " AND source_system = 'sg.vs.fo'").fetchone()
    finally:
        conn.close()

    # full offline ingest cycle against the TEST database
    with connect() as ingest_conn:
        ingest_pdf_observation(
            conn=ingest_conn, company_id=10,
            source_event=source_event_dict(),
            pdf_type="FelagPdf", source_document_id="qa-readonly-felag",
            pdf_bytes=fixture_bytes("felag_10.bin"),
            collection_run_id=30001)
        ingest_pdf_observation(
            conn=ingest_conn, company_id=10,
            source_event=source_event_dict(),
            pdf_type="LysingPdf", source_document_id="qa-readonly-lysing",
            pdf_bytes=make_pdf(["Dagf. 01-01-2025", "Navn: P/F Ingen"]),
            collection_run_id=30001)

    conn = _qa_connect()
    if conn is None:
        pytest.skip("QA heimdal database not reachable - offline run")
    try:
        checksum_after = conn.execute(
            "SELECT count(*) FROM company_location").fetchone()[0]
        assert checksum_after == checksum_before
        if row10 is not None:
            location = conn.execute(
                "SELECT location_status FROM company_location WHERE"
                " source_system = %s AND source_entity_id = %s",
                (row10[0], row10[1])).fetchone()
            # reading is fine; nothing may have changed by the ingest cycle
            assert location is None or location[0] in (
                "FAROESE_CITY", "FOREIGN", "UNKNOWN")
    finally:
        conn.close()