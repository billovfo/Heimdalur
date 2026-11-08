#!/usr/bin/env python3
"""WP-C3 checks: control registration (cbTypeId=41) and O.V.E. status (81).

Every live-data check scopes to the frozen input set (WP-C1): get-table
HTTP-200 attempts for FETCHED companies. Expected counts are frozen with the
input set itself, like test_heimdal_relations.py's.

The acceptance criteria under test:
  * every cbTypeId=41 raw pair is one REGISTERED_CONTROL_ACTOR canonical row
    with no percentage field populated;
  * every cbTypeId=81 raw row is exactly one of (a) an ENTITY_STATUS
    NO_REGISTERED_CONTROL_ACTOR on that entity (self-edge), or (b) an
    OTHER_SOURCE_RELATION with UNRESOLVED_SOURCE_ANOMALY (From != To) —
    nothing dropped, nothing double-counted, no canonical O.V.E. self-edge;
  * zero REGISTERED_CONTROL_ACTOR rows reachable from any ownership view or
    calculation (structural: recomputation + view-purity + schema constraint);
  * control actors stay clickable (canonical_entity rows exist).

Run: python3 test_heimdal_control_ove.py
"""
import os
import psycopg

from heimdal.pdfhist.db import qa_dsn
from heimdal_collect import find_cycle_components, compute_indirect_ownership

INPUT_SET_JOIN = (
    " FROM request_attempt ra"
    " JOIN company_inventory ci ON ci.source_entity_id = ra.requested_source_entity_id"
    " JOIN response_content rc ON rc.response_sha256 = ra.response_sha256")
INPUT_SET_WHERE = (
    " WHERE ra.endpoint='get-table' AND ra.http_status=200"
    " AND ci.inventory_status='FETCHED'")

# Frozen with the input set (reconciled 2026-08-14): 353 raw O.V.E. rows —
# 338 self-edge rows (338 distinct entities, one per entity's own body),
# 15 non-self rows over 8 distinct pairs (7 pairs seen from both endpoints,
# 1 seen once).
OVE_SELF_ROWS = 338
OVE_ANOMALY_RAW_ROWS = 15
OVE_ANOMALY_PAIRS = 8
REGISTERED_CONTROL_ACTOR_ROWS = 5311

# One of the 8 §15.3 anomalies, kept as fixture (source contract's list).
ANOMALY_FIXTURE = (5990, 5991)   # Varðin #931 <-> Varðin Invest #6240


def _db():
    return psycopg.connect(qa_dsn())


def _raw_81_rows(conn):
    """Every cbTypeId=81 raw row in the frozen input set."""
    return conn.execute(
        "SELECT (e.value->>'fromId')::bigint, (e.value->>'toId')::bigint"
        + INPUT_SET_JOIN
        + ", LATERAL json_array_elements(convert_from(rc.response_bytes,'UTF8')::json) e"
        + INPUT_SET_WHERE
        + " AND (e.value->>'cbTypeId')::int = 81").fetchall()


def _raw_41_pairs(conn):
    return conn.execute(
        "SELECT DISTINCT (e.value->>'fromId')::bigint, (e.value->>'toId')::bigint"
        + INPUT_SET_JOIN
        + ", LATERAL json_array_elements(convert_from(rc.response_bytes,'UTF8')::json) e"
        + INPUT_SET_WHERE
        + " AND (e.value->>'cbTypeId')::int = 41").fetchall()


def test_control_actors_one_canonical_row_each_no_percentage():
    """Criterion 1: every cbTypeId=41 raw pair is one REGISTERED_CONTROL_ACTOR
    canonical row; no percentage column is populated on any of them."""
    with _db() as conn:
        raw = set(_raw_41_pairs(conn))
        canonical = conn.execute(
            "SELECT raw_from_source_entity_id, raw_to_source_entity_id"
            " FROM registry_ownership.relation"
            " WHERE canonical_relation_type='REGISTERED_CONTROL_ACTOR'").fetchall()
        assert len(canonical) == REGISTERED_CONTROL_ACTOR_ROWS, len(canonical)
        assert raw == set(canonical)                    # no missing, no extra rows
        n, pct_label, pct_value, pct_status = conn.execute(
            "SELECT count(*), count(raw_percentage_label), count(parsed_percentage),"
            " count(percentage_parse_status) FROM registry_ownership.relation"
            " WHERE canonical_relation_type='REGISTERED_CONTROL_ACTOR'").fetchone()
        assert n == REGISTERED_CONTROL_ACTOR_ROWS
        assert pct_label == pct_value == pct_status == 0


def test_control_actors_clickable_entities():
    """Criterion 3: every control-actor endpoint has a canonical_entity row."""
    with _db() as conn:
        missing = conn.execute(
            "SELECT count(*) FROM ("
            "  SELECT canonical_subject_entity_id e FROM registry_ownership.relation"
            "   WHERE canonical_relation_type='REGISTERED_CONTROL_ACTOR'"
            "  UNION SELECT canonical_object_entity_id FROM registry_ownership.relation"
            "   WHERE canonical_relation_type='REGISTERED_CONTROL_ACTOR') ep"
            " LEFT JOIN canonical_entity ce ON ce.source_entity_id = ep.e"
            " WHERE ce.canonical_entity_id IS NULL").fetchone()[0]
        assert missing == 0, missing


def test_ove_two_bucket_accounting():
    """Criterion 2: every raw O.V.E. row is exactly one status or one
    anomaly — nothing dropped, nothing double-counted."""
    with _db() as conn:
        raw = _raw_81_rows(conn)
        self_rows = [(f, t) for f, t in raw if f == t]
        anomaly_rows = [(f, t) for f, t in raw if f != t]
        assert len(raw) == OVE_SELF_ROWS + OVE_ANOMALY_RAW_ROWS, len(raw)
        assert len(self_rows) == OVE_SELF_ROWS, len(self_rows)
        assert len(anomaly_rows) == OVE_ANOMALY_RAW_ROWS, len(anomaly_rows)

        # Bucket (a): one ENTITY_STATUS row per distinct self-edge entity.
        status_entities = {r[0] for r in conn.execute(
            "SELECT source_entity_id FROM entity_status"
            " WHERE canonical_status_type='NO_REGISTERED_CONTROL_ACTOR'").fetchall()}
        assert status_entities == {f for f, _ in self_rows}
        assert len(status_entities) == OVE_SELF_ROWS

        # Bucket (b): one OTHER_SOURCE_RELATION anomaly row per distinct pair.
        anomaly_pairs = set(anomaly_rows)
        canonical = conn.execute(
            "SELECT raw_from_source_entity_id, raw_to_source_entity_id,"
            " canonical_relation_type, semantic_status,"
            " semantic_direction_confidence,"
            " canonical_subject_entity_id, canonical_object_entity_id,"
            " raw_percentage_label, parsed_percentage, percentage_parse_status"
            " FROM registry_ownership.relation WHERE raw_source_relation_type_id=81").fetchall()
        assert len(anomaly_pairs) == OVE_ANOMALY_PAIRS, anomaly_pairs
        assert {r[:2] for r in canonical} == anomaly_pairs
        for r in canonical:
            assert r[2] == "OTHER_SOURCE_RELATION"
            assert r[3] == "UNRESOLVED_SOURCE_ANOMALY"
            assert r[4] == "UNCONFIRMED"                # never guessed
            assert r[5] is None and r[6] is None        # no subject/object claimed
            assert r[7] is None and r[8] is None and r[9] is None   # no percentage


def test_ove_no_canonical_self_edge():
    """§14a.2: O.V.E. is never a canonical self-edge; no O.V.E. row exists in
    registry_ownership.relation except the 8 anomalies."""
    with _db() as conn:
        n, self_edges = conn.execute(
            "SELECT count(*),"
            " count(*) FILTER (WHERE raw_from_source_entity_id=raw_to_source_entity_id)"
            " FROM registry_ownership.relation WHERE raw_source_relation_type_id=81").fetchone()
        assert n == OVE_ANOMALY_PAIRS, n
        assert self_edges == 0, self_edges


def test_anomaly_preserved_verbatim():
    """One anomaly-preservation case: raw fields byte-identical, flagged
    UNRESOLVED_SOURCE_ANOMALY, direction never guessed."""
    f, t = ANOMALY_FIXTURE
    with _db() as conn:
        raw = conn.execute(
            "SELECT e.value->>'fromName', e.value->>'toName', e.value->>'cbTypeName'"
            + INPUT_SET_JOIN
            + ", LATERAL json_array_elements(convert_from(rc.response_bytes,'UTF8')::json) e"
            + INPUT_SET_WHERE
            + " AND (e.value->>'cbTypeId')::int = 81"
            " AND (e.value->>'fromId')::bigint=%s AND (e.value->>'toId')::bigint=%s",
            (f, t)).fetchall()
        assert raw, (f, t)
        canonical = conn.execute(
            "SELECT raw_from_source_entity_label, raw_to_source_entity_label,"
            " raw_source_relation_label, raw_source_relation_type_id,"
            " semantic_status, semantic_direction_confidence,"
            " canonical_subject_entity_id, canonical_object_entity_id"
            " FROM registry_ownership.relation WHERE raw_from_source_entity_id=%s"
            " AND raw_to_source_entity_id=%s AND raw_source_relation_type_id=81",
            (f, t)).fetchall()
        assert len(canonical) == 1, canonical
        r = canonical[0]
        assert (r[0], r[1], r[2], r[3]) == (raw[0][0], raw[0][1], raw[0][2], 81)
        assert r[4] == "UNRESOLVED_SOURCE_ANOMALY"
        assert r[5] == "UNCONFIRMED"
        assert r[6] is None and r[7] is None


def test_no_control_row_reachable_from_ownership_views():
    """Criterion 4: ownership views never surface a control row. v_direct_
    ownership filters on DIRECT_OWNERSHIP — a changed WHERE clause (or any
    other leak) shows up here as a non-DIRECT_OWNERSHIP relation_id."""
    with _db() as conn:
        n = conn.execute(
            "SELECT count(*) FROM v_direct_ownership v"
            " JOIN registry_ownership.relation cr USING (relation_id)"
            " WHERE cr.canonical_relation_type <> 'DIRECT_OWNERSHIP'").fetchone()[0]
        assert n == 0, n
        # v_control_registration has no percentage-shaped column at all.
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name='v_control_registration'").fetchall()}
        assert not any("pct" in c or "percentage" in c for c in cols), cols


def test_indirect_ownership_is_direct_ownership_only():
    """Criterion 4 (structural): registry_ownership.indirect_ownership is byte-identical to a
    recomputation from DIRECT_OWNERSHIP edges alone — if a control row were
    ever fed into the ownership math, the pair sets would diverge."""
    with _db() as conn:
        direct = [(o, w, float(p)) for o, w, p in conn.execute(
            "SELECT canonical_subject_entity_id, canonical_object_entity_id,"
            " parsed_percentage FROM registry_ownership.relation"
            " WHERE canonical_relation_type='DIRECT_OWNERSHIP'").fetchall()]
        components = find_cycle_components((o, w) for o, w, _ in direct)
        cycle_of = {n: i for i, comp in enumerate(components) for n in comp}
        results = compute_indirect_ownership(direct, cycle_of)
        recomputed = {(r["owner_entity_id"], r["owned_entity_id"]) for r in results}
        stored = set(conn.execute(
            "SELECT owner_entity_id, owned_entity_id"
            " FROM registry_ownership.indirect_ownership").fetchall())
        assert stored == recomputed
        assert len(stored) > 0


def test_percentage_on_non_ownership_rows_rejected_by_schema():
    """Criterion 1 (structural): the percentage invariant ties percentage
    columns to DIRECT_OWNERSHIP — a REGISTERED_CONTROL_ACTOR row carrying a
    percentage is rejected at the schema level, never just by convention."""
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
            " VALUES (%s, 'get-table', '{}', 999999, now(), 200, 'OK', 'test', NULL)"
            " RETURNING attempt_id", (run_id,)).fetchone()[0]

        base = {
            "raw_from_source_entity_id": 1, "raw_from_source_entity_label": "a",
            "raw_to_source_entity_id": 2, "raw_to_source_entity_label": "b",
            "raw_source_relation_type_id": 41, "raw_source_relation_label": "Veruligur Eigari",
            "canonical_relation_type": "REGISTERED_CONTROL_ACTOR",
            "canonical_subject_entity_id": 1, "canonical_object_entity_id": 2,
            "semantic_direction_confidence": "OBSERVED_FOR_SUPPLIED_EXAMPLES",
            "source_attempt_id": attempt_id,
        }

        def insert(fields):
            cols = ", ".join(fields)
            return conn.execute(
                f"INSERT INTO registry_ownership.relation ({cols})"
                f" VALUES ({', '.join('%s' for _ in fields)})", tuple(fields.values()))

        # Control row with a percentage: forbidden.
        try:
            with conn.transaction():
                insert({**base, "raw_percentage_label": "Veruligur Eigari 51%",
                        "parsed_percentage": 51.0, "percentage_parse_status": "PARSED"})
            raise AssertionError("REGISTERED_CONTROL_ACTOR row with percentage was accepted")
        except psycopg.errors.CheckViolation:
            pass
        # Plain control row without percentage columns: legal.
        insert(base)
        # DIRECT_OWNERSHIP row with percentage: still legal.
        insert({**base, "raw_source_relation_type_id": 1, "raw_source_relation_label": "Eigari 51%",
                "canonical_relation_type": "DIRECT_OWNERSHIP",
                "semantic_direction_confidence": "OBSERVED_AND_OWNER_ACCEPTED",
                "raw_percentage_label": "Eigari 51%", "parsed_percentage": 51.0,
                "percentage_parse_status": "PARSED"})
        conn.rollback()   # fixture rows never land


def test_portal_control_labels_carry_no_ejer_language():
    """Criterion 3: the §16.4 direction label renders per relation basis —
    a DIRECT_CONTROL_ACTOR context says 'er kontrolaktør', never 'ejer'."""
    macros = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "templates", "macros.html")
    src = open(macros, encoding="utf-8").read()
    assert "ejer/er kontrolaktør" not in src
    assert "er kontrolaktør" in src
    assert "'denne virksomhed ejer'" in src or '"denne virksomhed ejer"' in src


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} checks passed")
