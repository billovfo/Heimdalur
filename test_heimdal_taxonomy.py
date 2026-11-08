#!/usr/bin/env python3
"""WP-C4 checks: full-population taxonomy audit + role-group tagging.

Every live-data check scopes to the frozen input set (WP-C1): get-table
HTTP-200 attempts for FETCHED companies. Expected counts are frozen with the
input set itself, like test_heimdal_relations.py's 4527/4524.

The acceptance criteria under test:
  * the audit covers 100% of raw rows — the per-(cbTypeId, cbTypeName) sum
    equals the total raw row count (SoT §12 audit precondition);
  * every non-1/41/81 raw row is an OTHER_SOURCE_RELATION canonical row with
    the raw label verbatim — no type dropped, no type promoted;
  * every role-group tag carries confidence INFERRED, never OBSERVED
    (structural: the schema CHECK admits no other value);
  * voting rights (cbTypeId=101) stay out of roles and ownership lists;
  * portal display labels are gender-neutral (no gendered role word).

Run: python3 test_heimdal_taxonomy.py
"""
import psycopg

from heimdal.modules.entities.service import roles_at_company, voting_rights_at_company
from heimdal.pdfhist.db import qa_dsn
from heimdal_collect import classify_role

INPUT_SET_JOIN = (
    " FROM request_attempt ra"
    " JOIN company_inventory ci ON ci.source_entity_id = ra.requested_source_entity_id"
    " JOIN response_content rc ON rc.response_sha256 = ra.response_sha256")
INPUT_SET_WHERE = (
    " WHERE ra.endpoint='get-table' AND ra.http_status=200"
    " AND ci.inventory_status='FETCHED'")

# Frozen with the input set (reconciled 2026-08-14 via taxonomy-audit):
# 4,527 attempts, 21,871 raw rows, 18 distinct cbTypeIds.
TOTAL_RAW_ROWS = 21871
TYPE_RAW_COUNTS = {
    1: 8429, 21: 4977, 22: 747, 23: 4, 24: 1, 27: 1, 29: 33, 30: 9,
    32: 1881, 33: 4, 34: 61, 36: 7, 37: 46, 41: 5314, 81: 353, 101: 1,
    121: 2, 122: 1,
}
# One canonical home per distinct (from, to, cbTypeId) pair; 81 splits into
# 338 ENTITY_STATUS self-edges + 8 OTHER_SOURCE_RELATION anomalies.
TYPE_CANONICAL_COUNTS = {
    1: 6699, 21: 4977, 22: 747, 23: 4, 24: 1, 27: 1, 29: 33, 30: 9,
    32: 1881, 33: 4, 34: 61, 36: 7, 37: 46, 41: 5311, 81: 8, 101: 1,
    121: 2, 122: 1,
}
ENTITY_STATUS_ROWS = 338


def _db():
    return psycopg.connect(qa_dsn())


def _raw_rows(conn, type_id=None):
    """Every raw row in the frozen input set, optionally for one cbTypeId:
    (type, from_id, to_id, cbTypeName)."""
    where = ""
    params = []
    if type_id is not None:
        where = " AND (e.value->>'cbTypeId')::int = %s"
        params = [type_id]
    return conn.execute(
        "SELECT (e.value->>'cbTypeId')::int, (e.value->>'fromId')::bigint,"
        " (e.value->>'toId')::bigint, e.value->>'cbTypeName'"
        + INPUT_SET_JOIN
        + ", LATERAL json_array_elements(convert_from(rc.response_bytes,'UTF8')::json) e"
        + INPUT_SET_WHERE + where, params).fetchall()


def test_audit_sum_equals_total_raw_row_count():
    """Criterion 1: the audit enumerates every distinct (cbTypeId, cbTypeName)
    and its sum equals the total raw row count — 100% coverage. The per-type
    counts are frozen and asserted, so any raw-row drift fails here."""
    with _db() as conn:
        raw = _raw_rows(conn)
        assert len(raw) == TOTAL_RAW_ROWS, len(raw)
        per_type = {}
        per_label = {}
        for t, _f, _g, label in raw:
            per_type[t] = per_type.get(t, 0) + 1
            per_label[(t, label)] = per_label.get((t, label), 0) + 1
        assert sum(per_type.values()) == TOTAL_RAW_ROWS
        assert sum(per_label.values()) == TOTAL_RAW_ROWS
        assert per_type == TYPE_RAW_COUNTS, per_type


def test_every_raw_pair_has_exactly_one_canonical_home():
    """Criterion 2: per type, the distinct raw pairs equal the canonical rows
    (81: 8 anomalies + 338 statuses) — nothing dropped, nothing double-counted,
    no invented rows."""
    with _db() as conn:
        for t, raw_n in TYPE_RAW_COUNTS.items():
            pairs = {(f, g) for _, f, g, _ in _raw_rows(conn, t)}
            assert len(pairs) == TYPE_CANONICAL_COUNTS[t] + (ENTITY_STATUS_ROWS if t == 81 else 0), \
                (t, len(pairs))
        canonical = dict(conn.execute(
            "SELECT raw_source_relation_type_id, count(*) FROM registry_ownership.relation"
            " GROUP BY 1").fetchall())
        assert {t: canonical.get(t, 0) for t in TYPE_CANONICAL_COUNTS} == TYPE_CANONICAL_COUNTS
        assert canonical.get(81, 0) + ENTITY_STATUS_ROWS == 346


def test_other_source_relation_labels_verbatim_and_unconfirmed():
    """Criterion 2: every OTHER_SOURCE_RELATION row carries the raw cbTypeName
    verbatim, claims no subject/object, and no direction — never guessed."""
    with _db() as conn:
        raw_pairs = {t: {} for t in TYPE_RAW_COUNTS}
        for t, f, g, label in _raw_rows(conn):
            raw_pairs[t].setdefault((f, g), label)
        rows = conn.execute(
            "SELECT raw_from_source_entity_id, raw_to_source_entity_id,"
            " raw_source_relation_type_id, raw_source_relation_label,"
            " canonical_relation_type, canonical_subject_entity_id,"
            " canonical_object_entity_id, semantic_direction_confidence,"
            " raw_percentage_label, parsed_percentage, percentage_parse_status"
            " FROM registry_ownership.relation"
            " WHERE canonical_relation_type='OTHER_SOURCE_RELATION'").fetchall()
        for f, g, t, label, ctype, subj, obj, conf, rpl, pct, pps in rows:
            assert t != 1 and t != 41, t          # no decided type demoted
            assert ctype == "OTHER_SOURCE_RELATION"
            assert label == raw_pairs[t][(f, g)], (f, g, t, label)   # verbatim
            assert subj is None and obj is None, (f, g, t)
            assert conf == "UNCONFIRMED", (f, g, t, conf)
            assert rpl is None and pct is None and pps is None, (f, g, t)


def test_no_unknown_type_dropped_or_promoted():
    """Criterion 2 (no drop, no promotion): every non-1/41/81 canonical row is
    OTHER_SOURCE_RELATION, and every distinct non-1/41/81 raw pair has its
    canonical OTHER_SOURCE_RELATION row."""
    with _db() as conn:
        canonical = set(conn.execute(
            "SELECT raw_from_source_entity_id, raw_to_source_entity_id,"
            " raw_source_relation_type_id FROM registry_ownership.relation"
            " WHERE raw_source_relation_type_id NOT IN (1, 41, 81)").fetchall())
        raw = {(f, g, t) for t, f, g, _ in _raw_rows(conn) if t not in (1, 41, 81)}
        assert raw == canonical                    # exact set equality, both ways
        promoted = conn.execute(
            "SELECT count(*) FROM registry_ownership.relation"
            " WHERE canonical_relation_type <> 'OTHER_SOURCE_RELATION'"
            " AND raw_source_relation_type_id NOT IN (1, 41)").fetchone()[0]
        assert promoted == 0, promoted


def test_every_role_group_tag_is_inferred():
    """Criterion 3: every canonical row is tagged, and every tag's confidence
    is INFERRED — the source's group taxonomy is never attached as observed.
    Structural: the schema CHECK admits no confidence value but INFERRED."""
    with _db() as conn:
        untagged = conn.execute(
            "SELECT count(*) FROM registry_ownership.relation"
            " WHERE inferred_group IS NULL").fetchone()[0]
        assert untagged == 0, untagged
        non_inferred = conn.execute(
            "SELECT count(*) FROM registry_ownership.relation"
            " WHERE inferred_group_confidence <> 'INFERRED'").fetchone()[0]
        assert non_inferred == 0, non_inferred
        # Structural: OBSERVED must be rejected by the CHECK constraint.
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
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO registry_ownership.relation (raw_from_source_entity_id,"
                    " raw_from_source_entity_label, raw_to_source_entity_id,"
                    " raw_to_source_entity_label, raw_source_relation_type_id,"
                    " raw_source_relation_label, canonical_relation_type,"
                    " semantic_direction_confidence, inferred_group,"
                    " inferred_group_confidence, canonical_role_label, source_attempt_id)"
                    " VALUES (1,'a',2,'b',21,'Stjóri','OTHER_SOURCE_RELATION',"
                    " 'UNCONFIRMED','STJORN','OBSERVED','Stjóri',%s)", (attempt_id,))
            raise AssertionError("OBSERVED group confidence was accepted")
        except psycopg.errors.CheckViolation:
            pass
        conn.rollback()


def test_voting_rights_excluded_from_roles_and_ownership():
    """Criterion 4: cbTypeId=101 is in the audit, stored as OTHER_SOURCE_RELATION,
    and never lands in the portal's role groups nor in any ownership view."""
    with _db() as conn:
        raw_101 = _raw_rows(conn, 101)
        assert len(raw_101) == 1 and raw_101[0][3] == "Atkvøðurættur 100.0%"
        rows = conn.execute(
            "SELECT canonical_relation_type, canonical_subject_entity_id,"
            " canonical_object_entity_id, semantic_direction_confidence"
            " FROM registry_ownership.relation WHERE raw_source_relation_type_id=101").fetchall()
        assert rows == [("OTHER_SOURCE_RELATION", None, None, "UNCONFIRMED")], rows
        # Not reachable through any ownership view or a role group.
        for view in ("v_direct_ownership", "v_control_registration"):
            n = conn.execute(
                f"SELECT count(*) FROM {view} v"
                " JOIN registry_ownership.relation cr USING (relation_id)"
                " WHERE cr.raw_source_relation_type_id = 101").fetchone()[0]
            assert n == 0, (view, n)
        n = conn.execute(
            "SELECT count(*) FROM v_role_relation"
            " WHERE raw_source_relation_type_id = 101"
            " AND inferred_group <> 'EIGARAR'").fetchone()[0]
        assert n == 0, n
        # Portal-level: the voting-rights company's role groups never include
        # the 101 row; its own section does.
        company_id = conn.execute(
            "SELECT company_id FROM v_role_relation"
            " WHERE raw_source_relation_type_id = 101").fetchone()[0]
        prow = psycopg.connect(qa_dsn(),
                               row_factory=psycopg.rows.dict_row)
        groups = roles_at_company(prow, company_id)
        voting = voting_rights_at_company(prow, company_id)
        prow.close()
        assert voting, company_id
        for rows_ in groups.values():
            for row in rows_:
                assert row["inferred_group"] != "EIGARAR"


def test_role_display_labels_gender_neutral():
    """Criterion 5: no display label presumes grammatical gender about a person
    (owner instruction 2026-08-02: Næstforkvinna -> Næstformaður,
    Nevndarforkvinna -> Nevndarformaður)."""
    with _db() as conn:
        gendered = conn.execute(
            "SELECT count(*) FROM registry_ownership.relation"
            " WHERE canonical_role_label LIKE '%kvinna%'"
            " OR canonical_role_label LIKE '%forkvinn%'").fetchone()[0]
        assert gendered == 0, gendered
        # The two raw feminine spellings collapse onto the neutral labels.
        for t, neutral in ((34, "Nevndarformaður"), (30, "Næstformaður")):
            labels = set(conn.execute(
                "SELECT DISTINCT canonical_role_label FROM registry_ownership.relation"
                " WHERE raw_source_relation_type_id = %s", (t,)).fetchall())
            assert labels == {(neutral,)}, (t, labels)
            assert classify_role(t)[1] == neutral
        # Every known type maps to some group — an unmapped type would surface
        # as an untagged row (caught above), and its classify_role here.
        for t in TYPE_RAW_COUNTS:
            assert classify_role(t)[0] is not None, t


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} checks passed")
