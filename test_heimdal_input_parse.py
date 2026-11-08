#!/usr/bin/env python3
"""WP-C1 checks: frozen input-set enumeration + per-body parse harness.

Run: python3 test_heimdal_input_parse.py
"""
import hashlib
import json

import psycopg

from heimdal.pdfhist.db import qa_dsn
from heimdal_collect import (GET_TABLE_KEYS, fingerprint_input_set, input_failures,
                             input_set_rows, parse_inputs,
                             validate_get_table_body)


def _db():
    return psycopg.connect(qa_dsn())


def _valid_row(**over):
    row = {"fromId": 1, "fromName": "a - T, x", "cbTypeId": 1,
           "cbTypeName": "Eigari 80.0%", "toId": 2, "toName": "b - T, y"}
    row.update(over)
    return row


def _table_rows():
    with _db() as conn:
        return sorted(conn.execute(
            "SELECT response_sha256, parse_status, failure_reason, row_count,"
            " company_count FROM input_parse_status").fetchall())


def _table_fingerprint():
    return hashlib.sha256(json.dumps(_table_rows()).encode()).hexdigest()


def test_empty_body_fails():
    assert validate_get_table_body(b"") == ("FAILED", "EMPTY_BODY", None)
    assert validate_get_table_body(b"   ") == ("FAILED", "EMPTY_BODY", None)


def test_non_json_fails():
    assert validate_get_table_body(b"not json at all") == ("FAILED", "NON_JSON", None)
    assert validate_get_table_body(b"<html></html>") == ("FAILED", "NON_JSON", None)
    assert validate_get_table_body(b"\xff\xfe\x00 binary") == ("FAILED", "NON_JSON", None)


def test_not_array_fails():
    assert validate_get_table_body(b'{"fromId": 1}') == ("FAILED", "NOT_ARRAY", None)
    assert validate_get_table_body(b'"just a string"') == ("FAILED", "NOT_ARRAY", None)


def test_bad_row_keys_fail():
    missing = _valid_row()
    del missing["toName"]
    assert validate_get_table_body(
        json.dumps([missing]).encode()) == ("FAILED", "BAD_ROW_KEYS", None)
    extra = _valid_row(otherKey=1)
    assert validate_get_table_body(
        json.dumps([extra]).encode()) == ("FAILED", "BAD_ROW_KEYS", None)
    assert validate_get_table_body(
        json.dumps([1, 2]).encode()) == ("FAILED", "BAD_ROW_KEYS", None)


def test_valid_rows_ok():
    assert validate_get_table_body(b"[]") == ("OK", None, 0)
    assert validate_get_table_body(
        json.dumps([_valid_row(), _valid_row(fromId=9)]).encode()) == ("OK", None, 2)


def test_enumeration_reconciles_with_expected_counts():
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM request_attempt ra"
            " JOIN company_inventory ci ON ci.source_entity_id = ra.requested_source_entity_id"
            " WHERE ra.endpoint='get-table' AND ra.http_status=200"
            " AND ci.inventory_status='FETCHED'")
        expected_rows = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FILTER (WHERE parse_status='OK'),"
            " SUM(company_count) FILTER (WHERE parse_status='OK'),"
            " COUNT(*) FILTER (WHERE parse_status='FAILED')"
            " FROM input_parse_status")
        ok_bodies, ok_companies, failed = cur.fetchone()
        cur.execute("SELECT count(*) FROM input_parse_status")
        status_rows = cur.fetchone()[0]
    # H-5: invariants instead of the 2026-08 counts (4,527 rows / 4,524 bodies)
    assert expected_rows > 0, expected_rows
    assert ok_companies == expected_rows, (ok_companies, expected_rows)
    assert failed == 0, failed
    assert 0 < ok_bodies == status_rows <= ok_companies, (ok_bodies, status_rows)


def test_rerun_is_idempotent():
    before = _table_fingerprint()
    with _db() as conn:
        report = parse_inputs(conn)
    after = _table_fingerprint()
    assert before == after
    with _db() as conn:
        rows = input_set_rows(conn)
    assert report["input_rows"] == len(rows) > 0
    assert report["distinct_bodies"] == len(_table_rows())


def test_fingerprint_is_deterministic():
    bodies = {"abc": [b"[]", 4, [1, 2, 3, 4]], "def": [b"[]", 1, [5]]}
    assert fingerprint_input_set(bodies) == fingerprint_input_set(dict(bodies))


def test_keys_match_shape_definition():
    assert GET_TABLE_KEYS == {"fromId", "fromName", "cbTypeId", "cbTypeName", "toId", "toName"}


def test_input_set_enumeration_is_deterministic():
    with _db() as conn:
        first = input_set_rows(conn)
        second = input_set_rows(conn)
    assert first == second
    assert first


def test_failure_itemization_scopes_to_input_set():
    """Review fix: a FAILED body whose sha is shared by a non-get-table attempt
    must not list companies outside the frozen input set. Under the old
    sha-only join this test fails (company 789999 would be itemized)."""
    sha = "e" * 64
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO collection_run (run_type, collector_version, config,"
            " permission_status, permission_scope, owner_attestation_recorded_at,"
            " source_owner_contact_required_before_collection, source_support_availability)"
            " VALUES ('RELATION_PROCESSING','test','{}','GRANTED','TEST','2026-08-14',false,'TEST')"
            " RETURNING collection_run_id")
        run_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO response_content (response_sha256, response_bytes)"
            " VALUES (%s, %s) ON CONFLICT DO NOTHING", (sha, b'{"oops": 1}'))
        cur.execute(
            "INSERT INTO company_inventory (source_system, source_entity_id,"
            " raw_display_name, normalized_search_name, typing_status,"
            " discovery_method, inventory_run_id, raw_response_reference,"
            " inventory_status)"
            " VALUES ('test', %s, 'X', 'x', 'UNKNOWN_ENTITY', 'test', %s, %s, 'FETCHED')"
            " ON CONFLICT (source_system, source_entity_id) DO NOTHING",
            (456, run_id, sha))
        cur.execute(
            "INSERT INTO request_attempt (collection_run_id, endpoint, parameters,"
            " requested_source_entity_id, requested_at, http_status, error_category,"
            " collector_version, response_sha256)"
            " VALUES (%s, 'get-table', '{}', 456, now(), 200, 'OK', 'test', %s),"
            "        (%s, 'search-cbi', '{}', 789999, now(), 200, 'OK', 'test', %s)",
            (run_id, sha, run_id, sha))
        cur.execute(
            "INSERT INTO input_parse_status (response_sha256, parse_status,"
            " failure_reason, row_count, company_count)"
            " VALUES (%s, 'FAILED', 'NOT_ARRAY', NULL, 1)"
            " ON CONFLICT (response_sha256) DO UPDATE SET parse_status='FAILED',"
            " failure_reason='NOT_ARRAY'", (sha,))
        try:  # never committed: the raw tier is append-only (A-14), so roll back instead
            failures = input_failures(conn)
        finally:
            conn.rollback()
    assert len(failures) == 1
    assert failures[0][0] == sha
    assert failures[0][3] == [456], failures[0]
    assert 789999 not in failures[0][3]


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} checks passed")
