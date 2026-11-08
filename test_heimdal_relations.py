#!/usr/bin/env python3
"""WP-C2 checks: direct-ownership extraction against the live DB.

Every live-data check below scopes to the frozen input set (WP-C1): get-table
HTTP-200 attempts for FETCHED companies. The expected counts are frozen with
the input set itself, like test_heimdal_input_parse.py's 4527/4524.

Run: python3 test_heimdal_relations.py
"""
import psycopg

from heimdal.pdfhist.db import qa_dsn
from heimdal_collect import classify_edge

INPUT_SET_JOIN = (
    " FROM request_attempt ra"
    " JOIN company_inventory ci ON ci.source_entity_id = ra.requested_source_entity_id"
    " JOIN response_content rc ON rc.response_sha256 = ra.response_sha256")
INPUT_SET_WHERE = (
    " WHERE ra.endpoint='get-table' AND ra.http_status=200"
    " AND ci.inventory_status='FETCHED'")

# The 5 known self-edges (source entities 44, 1820, 4243, 5319, 5930):
# raw From == raw To, cbTypeId=1, stored as-is (§5.1's DIRECT_OWNERSHIP path
# applies to self-edges verbatim — no self-edge suppression for ownership).
SELF_EDGE_FIXTURES = {
    44: "Eigari 10.31%",
    1820: "Eigari 51.0%",
    4243: "Eigari 9.09%",
    5319: "Eigari 20.0%",
    5930: "Eigari 16.67%",
}


def _db():
    return psycopg.connect(qa_dsn())


def _raw_ownership_rows(conn):
    """Every cbTypeId=1 raw row in the frozen input set: (fromId, toId)."""
    return conn.execute(
        "SELECT (e.value->>'fromId')::bigint, (e.value->>'toId')::bigint"
        + INPUT_SET_JOIN
        + ", LATERAL json_array_elements(convert_from(rc.response_bytes,'UTF8')::json) e"
        + INPUT_SET_WHERE
        + " AND (e.value->>'cbTypeId')::int = 1").fetchall()


def test_reconciliation_counts_add_up():
    """8,429 raw rows = 6,699 canonical DIRECT_OWNERSHIP rows + 1,730 dedup
    duplicates, where a duplicate is the same (from, to) pair seen from both
    endpoints' get-table responses (§5.4: one canonical row per
    (from, to, cbTypeId) per snapshot, raw duplicates stay in the raw layer)."""
    with _db() as conn:
        raw = _raw_ownership_rows(conn)
        distinct = set(raw)
        canonical = conn.execute(
            "SELECT raw_from_source_entity_id, raw_to_source_entity_id"
            " FROM registry_ownership.relation"
            " WHERE canonical_relation_type='DIRECT_OWNERSHIP'").fetchall()
        canonical_set = set(canonical)
    from collections import Counter
    dup_pairs = {p for p, n in Counter(raw).items() if n > 1}
    assert len(raw) == 8429, len(raw)
    assert len(distinct) == 6699, len(distinct)
    assert len(dup_pairs) == 1730, len(dup_pairs)
    assert len(raw) == len(distinct) + len(dup_pairs)          # every dup seen exactly twice
    assert len(canonical) == len(distinct) == 6699              # one canonical row per raw pair
    assert canonical_set == distinct                            # no missing, no extra rows


def test_every_dedup_pair_has_exactly_one_canonical_row():
    with _db() as conn:
        from collections import Counter
        counts = Counter(_raw_ownership_rows(conn))
        dup_pairs = [(f, t) for (f, t), n in counts.items() if n > 1]
        for f, t in dup_pairs:
            n = conn.execute(
                "SELECT count(*) FROM registry_ownership.relation"
                " WHERE canonical_relation_type='DIRECT_OWNERSHIP'"
                " AND raw_from_source_entity_id=%s AND raw_to_source_entity_id=%s",
                (f, t)).fetchone()[0]
            assert n == 1, (f, t, n)


def test_percentage_parse_failures_itemized_zero():
    """Live itemization: every DIRECT_OWNERSHIP row is PARSED, and no FAILED
    row carries a percentage. (The forbidden FAILED-with-value shapes are
    rejected at the schema level — see the fixture test below.)"""
    with _db() as conn:
        rows = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE percentage_parse_status='PARSED'),"
            " count(*) FILTER (WHERE percentage_parse_status='FAILED'),"
            " count(*) FILTER (WHERE percentage_parse_status='FAILED'"
            "                   AND parsed_percentage IS NOT NULL)"
            " FROM registry_ownership.relation WHERE canonical_relation_type='DIRECT_OWNERSHIP'").fetchone()
    assert rows[0] == 6699
    assert rows[1] == 6699
    assert rows[2] == 0      # zero parse failures live — itemized: none
    assert rows[3] == 0      # and certainly none converted to a value


def test_parse_failure_never_zero_is_enforced_by_schema():
    """Fixture-based never-zero case (no live failures exist): the
    canonical_relation_percentage_invariant CHECK rejects a FAILED row
    carrying 0.0 — a parse failure can never silently become 0% (§14).
    The legal shapes (FAILED + NULL, PARSED + value) must pass."""
    with _db() as conn:
        run_id = conn.execute(
            "INSERT INTO collection_run (run_type, collector_version, config,"
            " permission_status, permission_scope, owner_attestation_recorded_at,"
            " source_owner_contact_required_before_collection, source_support_availability)"
            " VALUES ('RELATION_PROCESSING','test','{}','GRANTED','TEST','2026-08-14',false,'TEST')"
            " RETURNING collection_run_id").fetchone()[0]
        attempt_id = conn.execute(
            "INSERT INTO request_attempt (collection_run_id, endpoint, parameters,"
            " requested_source_entity_id, requested_at, http_status, error_category,"
            " collector_version, response_sha256)"
            " VALUES (%s, 'get-table', '{}', 999998, now(), 200, 'OK', 'test', NULL)"
            " RETURNING attempt_id", (run_id,)).fetchone()[0]

        def insert(fields):
            base = {
                "raw_from_source_entity_id": 1, "raw_from_source_entity_label": "a",
                "raw_to_source_entity_id": 2, "raw_to_source_entity_label": "b",
                "raw_source_relation_type_id": 1, "raw_source_relation_label": "Eigari x%",
                "canonical_relation_type": "DIRECT_OWNERSHIP",
                "canonical_subject_entity_id": 1, "canonical_object_entity_id": 2,
                "semantic_direction_confidence": "OBSERVED_AND_OWNER_ACCEPTED",
                "source_attempt_id": attempt_id,
            }
            base.update(fields)
            cols = ", ".join(base)
            return conn.execute(
                f"INSERT INTO registry_ownership.relation ({cols})"
                f" VALUES ({', '.join('%s' for _ in base)})", tuple(base.values()))

        # FAILED + 0.0 is the forbidden shape — the invariant must reject it.
        # (Its own savepoint: an expected CheckViolation aborts the enclosing
        # transaction, which would break the legal-shape inserts below.)
        try:
            with conn.transaction():
                insert({"raw_percentage_label": "Eigari mysterious",
                        "parsed_percentage": 0.0, "percentage_parse_status": "FAILED"})
            raise AssertionError("FAILED row with 0.0 was accepted")
        except psycopg.errors.CheckViolation:
            pass
        # FAILED + NULL is the legal failure shape.
        insert({"raw_percentage_label": "Eigari mysterious",
                "parsed_percentage": None, "percentage_parse_status": "FAILED"})
        # PARSED + value is the legal success shape.
        insert({"raw_from_source_entity_id": 3, "raw_to_source_entity_id": 4,
                "raw_percentage_label": "Eigari 10.0%",
                "parsed_percentage": 10.0, "percentage_parse_status": "PARSED"})
        conn.rollback()   # fixture rows never land


def test_classify_edge_parse_failure_is_failed_and_null_never_zero():
    _, f = classify_edge({"fromId": 1, "fromName": "a", "toId": 2, "toName": "b",
                          "cbTypeId": 1, "cbTypeName": "Eigari mysterious"})
    assert f["percentage_parse_status"] == "FAILED"
    assert f["parsed_percentage"] is None


def test_five_self_edge_fixtures_present_and_unaltered():
    """The 5 known self-edges: raw From/To/percentage stored verbatim —
    labels byte-identical, From == To, parsed value equal to the label."""
    with _db() as conn:
        raw = conn.execute(
            "SELECT (e.value->>'fromId')::bigint, e.value->>'fromName',"
            " e.value->>'toName', e.value->>'cbTypeName'"
            + INPUT_SET_JOIN
            + ", LATERAL json_array_elements(convert_from(rc.response_bytes,'UTF8')::json) e"
            + INPUT_SET_WHERE
            + " AND (e.value->>'cbTypeId')::int = 1 AND e.value->>'fromId' = e.value->>'toId'"
            " AND (e.value->>'fromId')::bigint = ANY(%s)",
            (sorted(SELF_EDGE_FIXTURES),)).fetchall()
        assert len(raw) == 5, raw
        for from_id, from_name, to_name, cb_name in raw:
            canonical = conn.execute(
                "SELECT raw_from_source_entity_label, raw_to_source_entity_label,"
                " raw_source_relation_label, raw_percentage_label, parsed_percentage,"
                " percentage_parse_status, canonical_subject_entity_id,"
                " canonical_object_entity_id"
                " FROM registry_ownership.relation WHERE canonical_relation_type='DIRECT_OWNERSHIP'"
                " AND raw_from_source_entity_id=%s AND raw_to_source_entity_id=%s",
                (from_id, from_id)).fetchall()
            assert len(canonical) == 1, (from_id, canonical)
            r = canonical[0]
            assert from_id in SELF_EDGE_FIXTURES
            assert r[2] == r[3] == SELF_EDGE_FIXTURES[from_id]   # cbTypeName verbatim
            assert r[0] == from_name and r[1] == to_name          # raw labels verbatim
            assert abs(float(r[4]) - float(SELF_EDGE_FIXTURES[from_id].split()[1].rstrip('%'))) < 0.0005
            assert r[5] == "PARSED"
            assert r[6] == r[7] == from_id                        # From == To, never swapped


def test_forbidden_classifications():
    """No Veruligur Eigari (41), O.V.E. (81) or voting-rights (101) row may be
    DIRECT_OWNERSHIP; DIRECT_OWNERSHIP carries only cbTypeId=1; raw From/To are
    never swapped or normalized away (canonical subject/object mirror them)."""
    with _db() as conn:
        assert conn.execute(
            "SELECT count(*) FROM registry_ownership.relation"
            " WHERE canonical_relation_type='DIRECT_OWNERSHIP'"
            " AND raw_source_relation_type_id <> 1").fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM registry_ownership.relation"
            " WHERE canonical_relation_type='DIRECT_OWNERSHIP'"
            " AND raw_source_relation_type_id IN (41, 81, 101)").fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM registry_ownership.relation"
            " WHERE canonical_relation_type='DIRECT_OWNERSHIP'"
            " AND (canonical_subject_entity_id <> raw_from_source_entity_id"
            "   OR canonical_object_entity_id <> raw_to_source_entity_id)").fetchone()[0] == 0
        # 41/81/101 stay in their own categories, never ownership
        for cb in (41, 81, 101):
            assert conn.execute(
                "SELECT count(*) FROM registry_ownership.relation"
                " WHERE raw_source_relation_type_id=%s"
                " AND canonical_relation_type='DIRECT_OWNERSHIP'", (cb,)).fetchone()[0] == 0


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} checks passed")
