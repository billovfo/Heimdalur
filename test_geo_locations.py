import pytest
import psycopg
from psycopg.errors import CheckViolation
from psycopg.rows import dict_row

from heimdal.pdfhist.db import qa_dsn

DB = qa_dsn()
REGIONS = {
    "SUDUROY", "SANDOY", "SUDURSTREYMOY", "NORDURSTREYMOY",
    "VAGAR", "EYSTUROY", "NORDOYGGJAR",
}
POSTBOX_POSTCODES = {
    "101": "Tórshavn", "102": "Tórshavn", "103": "Tórshavn", "110": "Tórshavn",
    "165": "Argir", "189": "Hoyvík", "211": "Sandur", "215": "Sandur",
    "351": "Vestmanna", "355": "Vestmanna", "371": "Miðvágur", "375": "Miðvágur",
    "381": "Sørvágur", "405": "Oyrarbakki", "461": "Norðskáli", "481": "Skála",
    "515": "Gøta", "516": "Gøta", "535": "Fuglafjørður", "610": "Saltangará",
    "621": "Runavík", "701": "Klaksvík", "710": "Klaksvík", "801": "Tvøroyri",
    "810": "Tvøroyri", "910": "Vágur",
}


def test_geo_postcode_seed_has_valid_city_coordinates():
    with psycopg.connect(DB, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT postal_code, city, region, latitude, longitude FROM geo_postcode"
        ).fetchall()
    assert rows
    assert {row["region"] for row in rows} == REGIONS
    assert all(len(row["postal_code"]) == 3 and row["postal_code"].isdigit() for row in rows)
    assert all(row["city"] and 61 < row["latitude"] < 63 for row in rows)
    assert all(-8 < row["longitude"] < -6 for row in rows)


def test_postbox_postcodes_use_canonical_city_groups():
    with psycopg.connect(DB, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT postal_code, city FROM geo_postcode WHERE postal_code = ANY(%s)",
            (list(POSTBOX_POSTCODES),),
        ).fetchall()
    assert {row["postal_code"]: row["city"] for row in rows} == POSTBOX_POSTCODES
    assert not any("boks" in row["city"].lower() for row in rows)


def test_company_location_enforces_one_allowed_status():
    with psycopg.connect(DB) as conn:
        statuses = conn.execute(
            "SELECT DISTINCT location_status FROM company_location"
        ).fetchall()
    assert {row[0] for row in statuses} <= {"FAROESE_CITY", "FOREIGN", "UNKNOWN"}


def test_company_location_upsert_is_idempotent():
    from heimdal_collect import SOURCE_SYSTEM, _upsert_company_location
    with psycopg.connect(DB) as conn:
        row = conn.execute(
            "SELECT ci.source_entity_id, ci.raw_display_name"
            " FROM company_inventory ci"
            " JOIN canonical_entity ce"
            "   ON ce.source_system = ci.source_system"
            "  AND ce.source_entity_id = ci.source_entity_id"
            " WHERE ce.entity_kind = 'COMPANY'"
            " ORDER BY ci.source_entity_id LIMIT 1"
        ).fetchone()
    assert row, "no COMPANY inventory row available to classify"
    source_entity_id, raw_display_name = row

    conn = psycopg.connect(DB)
    try:
        _upsert_company_location(conn, SOURCE_SYSTEM, source_entity_id, raw_display_name)
        _upsert_company_location(conn, SOURCE_SYSTEM, source_entity_id, raw_display_name)
        n, updated_at = conn.execute(
            "SELECT count(*), max(updated_at) FROM company_location"
            " WHERE source_system=%s AND source_entity_id=%s",
            (SOURCE_SYSTEM, source_entity_id)).fetchone()
    finally:
        conn.rollback()
        conn.close()
    assert n == 1
    assert updated_at is not None


def test_pdf_location_fetch_is_private_and_tracks_outcomes():
    with psycopg.connect(DB) as conn:
        columns = {row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='company_location_pdf_fetch'"
        )}
        grants = conn.execute(
            "SELECT grantee FROM information_schema.role_table_grants"
            " WHERE table_name='company_location_pdf_fetch'"
        ).fetchall()
    assert {"source_url", "response_sha256", "fetched_at", "outcome", "attempt_count"} <= columns
    assert ("heimdal_reader",) not in grants


def test_company_location_pdf_fetch_rejects_invalid_rows():
    with psycopg.connect(DB) as conn:
        source_system, source_entity_id = conn.execute(
            "SELECT source_system, source_entity_id FROM company_inventory LIMIT 1"
        ).fetchone()
        document_id = 1  # no FK to source.documents (owner D13): any id will do
        invalid_rows = [
            ("APPLIED", "NETWORK", 1),  # failure_kind without FAILED outcome
            ("APPLIED", None, 3),       # attempt_count out of range
        ]
        for outcome, failure_kind, attempt_count in invalid_rows:
            with pytest.raises(CheckViolation):
                conn.execute(
                    "INSERT INTO company_location_pdf_fetch"
                    " (source_system, source_entity_id, source_document_id, source_url,"
                    "  response_sha256, outcome, failure_kind, attempt_count)"
                    " VALUES (%s,%s,%s,%s,NULL,%s,%s,%s)",
                    (source_system, source_entity_id, document_id,
                     "https://example.invalid/x.pdf", outcome, failure_kind, attempt_count),
                )
            conn.rollback()


def test_pdf_candidate_join_strips_the_inventory_s_prefix():
    from heimdal_collect import select_pdf_location_candidates
    with psycopg.connect(DB) as conn:
        if conn.execute("SELECT to_regclass('source.documents')").fetchone()[0] is None:
            pytest.skip("no source.documents: the 2026-09-04 DB never had it (owner D13)")
        rows = select_pdf_location_candidates(conn)
    assert rows
    assert all(len(row) == 4 and row[3].startswith("https://www.skraseting.fo/") for row in rows)


def test_unknown_pdf_result_does_not_replace_existing_location(capsys):
    from heimdal_collect import apply_pdf_location_result
    fields = {"postal_code": None, "country_code": None, "location_status": "UNKNOWN"}
    with psycopg.connect(DB) as conn:
        source_system, source_entity_id, before = conn.execute(
            "SELECT source_system, source_entity_id, location_status"
            " FROM company_location WHERE location_status='FAROESE_CITY'"
            " ORDER BY source_entity_id LIMIT 1"
        ).fetchone()
        apply_pdf_location_result(conn, source_system, source_entity_id, fields)
        after = conn.execute(
            "SELECT location_status FROM company_location"
            " WHERE source_system=%s AND source_entity_id=%s",
            (source_system, source_entity_id),
        ).fetchone()[0]
        conn.rollback()
    assert after == before
    assert "Adressa:" not in capsys.readouterr().out


def test_city_clusters_and_coverage_reconcile_to_company_count():
    from heimdal.modules.geo.repository import select_city_clusters, select_location_coverage
    with psycopg.connect(DB, row_factory=dict_row) as conn:
        cities = select_city_clusters(conn)
        coverage = select_location_coverage(conn)
        total = conn.execute(
            "SELECT count(*) AS n FROM canonical_entity WHERE entity_kind = 'COMPANY'"
        ).fetchone()["n"]
    assert coverage["faroe_islands"] == sum(c["company_count"] for c in cities)
    assert coverage["faroe_islands"] + coverage["unknown"] + coverage["foreign"] == total
