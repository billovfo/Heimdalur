"""COV-HISTORICAL NAME_FALLBACK regressions (audit 2026-11 lane X3a).

Parser v2 drops a non-person change ("(…)") from a notice. Every later
positional COV key shifts by one and misses the frozen H1 map even though the
document, event and person name are unchanged. The frozen map therefore gets
a name index; a role whose positional key misses may link only when exactly
one frozen record matches its (document_sha256, source_event_id,
entity_normalized, role_type) and that record was not linked yet.

Isolated: throwaway HEIMDAL_TEST_DB only; the canonical side is a minimal
schema "canon" and registry_ownership inside the same test DB.
"""
import json

import psycopg
import pytest

from heimdal.pdfhist import entity_links as el
from heimdal.pdfhist import projection
from heimdal.pdfhist.cov_historical import cov_source_key
from heimdal.pdfhist.testsupport import ensure_test_database
from test_registry_projection import RUN, _seed_evidence

# --- synthetic frozen identities (names never come from the real register) --

C_SHIFT, R_SHIFT = 9811, "9811"
C_POS, R_POS = 9812, "9812"
C_DUP, R_DUP = 9813, "9813"
C_DIFF, R_DIFF = 9814, "9814"
C_USED, R_USED = 9815, "9815"
C_PAIR, R_PAIR = 9816, "9816"

P_SHIFT, P_POS, P_DUP1, P_DUP2, P_USED = 8101, 8102, 8103, 8104, 8105
P_PAIR1, P_PAIR2, P_GOOD = 8106, 8107, 8108

# The "A Shift" person sits at index 2 in parser v2; the frozen H1 map has it
# at index 3 (the frozen parse still carried the dropped pseudo change).
SHIFT_CHANGES = [
    {"type": "NAME_CHANGE", "action": "CHANGED", "entity": "P/F Shift",
     "entity_normalized": "p/f shift", "raw_text": "Nýtt navn: P/F Shift",
     "effective_date": "2021-05-01"},
    {"type": "PURPOSE_CHANGE", "action": "CHANGED", "entity": "handil",
     "entity_normalized": "handil", "raw_text": "Nýtt endamál: handil",
     "effective_date": "2021-05-02"},
    {"type": "BOARD_CHANGE", "action": "APPOINTED", "entity": "A Shift",
     "entity_normalized": "a shift", "raw_text": "Innkomin: A Shift",
     "registration_date": "2021-06-01"},
]

POS_CHANGES = [
    {"type": "BOARD_CHANGE", "action": "APPOINTED", "entity": "P Pos",
     "entity_normalized": "p pos", "raw_text": "Innkomin: P Pos",
     "registration_date": "2021-06-01"},
]

# One event, two roles whose names normalise to the same value. The raw
# column keeps a double space so the projection uniqueness constraint admits
# both; the linker collapses it.
DUP_CHANGES = [
    {"type": "BOARD_CHANGE", "action": "APPOINTED", "entity": "B Dup",
     "entity_normalized": "b dup", "raw_text": "Innkomin: B Dup",
     "registration_date": "2021-06-01"},
    {"type": "BOARD_CHANGE", "action": "REMOVED", "entity": "B  Dup",
     "entity_normalized": "b  dup", "raw_text": "Farin: B Dup",
     "registration_date": "2021-06-02"},
]

DIFF_CHANGES = [
    {"type": "BOARD_CHANGE", "action": "APPOINTED", "entity": "C Diff",
     "entity_normalized": "c diff", "raw_text": "Innkomin: C Diff",
     "registration_date": "2021-06-01"},
    {"type": "BOARD_CHANGE", "action": "APPOINTED", "entity": "G Good",
     "entity_normalized": "g good", "raw_text": "Innkomin: G Good",
     "registration_date": "2021-06-02"},
]

# Both persons moved up one position in parser v2: frozen 1/2, parse 0/1.
PAIR_CHANGES = [
    {"type": "BOARD_CHANGE", "action": "APPOINTED", "entity": "D One",
     "entity_normalized": "d one", "raw_text": "Innkomin: D One",
     "registration_date": "2021-06-01"},
    {"type": "BOARD_CHANGE", "action": "APPOINTED", "entity": "D Two",
     "entity_normalized": "d two", "raw_text": "Innkomin: D Two",
     "registration_date": "2021-06-02"},
]

USED_CHANGES = [
    {"type": "BOARD_CHANGE", "action": "APPOINTED", "entity": "E Used",
     "entity_normalized": "e used", "raw_text": "Innkomin: E Used",
     "registration_date": "2021-06-01"},
]


@pytest.fixture(scope="module")
def dsn():
    dsn = ensure_test_database()
    with psycopg.connect(dsn) as c:
        projection.apply_migration(c)
        _seed_evidence(c, C_SHIFT, R_SHIFT, SHIFT_CHANGES)
        _seed_evidence(c, C_POS, R_POS, POS_CHANGES)
        _seed_evidence(c, C_DUP, R_DUP, DUP_CHANGES)
        _seed_evidence(c, C_DIFF, R_DIFF, DIFF_CHANGES)
        _seed_evidence(c, C_USED, R_USED, USED_CHANGES)
        _seed_evidence(c, C_PAIR, R_PAIR, PAIR_CHANGES)
        c.execute("""
            CREATE SCHEMA canon;
            CREATE TABLE canon.company_inventory (registration_number text,
                source_entity_id bigint);
            CREATE TABLE canon.canonical_entity (canonical_entity_id bigint,
                source_entity_id bigint, entity_kind text, display_name text);
            CREATE TABLE canon.v_role_relation (company_id bigint,
                person_name text, person_id bigint, inferred_group text);
            CREATE SCHEMA registry_ownership;
            CREATE TABLE registry_ownership.build_meta (build_id int,
                schema_version text, source_relation_count int,
                source_relation_max_id int, max_source_attempt_id int,
                max_collection_run_id int, content_hash text, status text);
            CREATE TABLE registry_ownership.relation (relation_id int,
                raw_from_source_entity_id bigint,
                raw_from_source_entity_label text);
            CREATE TABLE registry_ownership.relation_evidence_pointer (
                relation_id int, source_attempt_id int, collection_run_id int);
            INSERT INTO registry_ownership.build_meta VALUES
                (1, 'phase1-v1', 0, 0, 0, 0, 'h', 'ACTIVE');
            -- review fix round 1 #2: synthetic known-stale targets (I-5:
            -- the real reference names never enter git)
            INSERT INTO canon.canonical_entity VALUES
                (8101, 8101, 'PERSON', 'A Shift'),
                (8102, 8102, 'PERSON', 'P Pos'),
                (8103, 8103, 'PERSON', 'B Dup'),
                (8104, 8104, 'PERSON', 'B Dup (second)'),
                (8105, 8105, 'PERSON', 'E Used'),
                (8106, 8106, 'PERSON', 'D One'),
                (8107, 8107, 'PERSON', 'D Two'),
                (8108, 8108, 'PERSON', 'G Good'),
                (8201, 8201, 'PERSON', 'Zzone Delta Testell'),
                (8202, 8202, 'PERSON', 'Zztwo Epsilon Testmat'),
                (8203, 8203, 'PERSON', 'Zzfourth Zeta Testsonne');
            """)
        c.commit()
    return dsn


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn) as c:
        yield c


def _canon(dsn):
    return psycopg.connect(dsn, options="-c search_path=canon,public")


def _shadow(conn, version):
    run = projection.build_shadow(conn, source_collection_run_id=RUN,
                                  projection_version=version)
    conn.commit()
    return run


def _roles(conn, run, company_id):
    return conn.execute(
        "SELECT id, role_type, entity_normalized, source_event_id, "
        "source_document_id, source_observation_id, source_parse_result_id "
        "FROM registry_projection.company_person_role "
        "WHERE projection_run_id=%s AND company_id=%s "
        "  AND source_scope='HISTORICAL_EVENT' ORDER BY id",
        (run, company_id)).fetchall()


def _record(conn, role, occurrence_index, canonical_person_id, name=None):
    (_, role_type, entity_normalized, event_id,
     _, observation_id, _) = role
    sha = conn.execute(
        "SELECT sha256 FROM pdf_document_observation WHERE id=%s",
        (observation_id,)).fetchone()[0]
    return {
        "document_sha256": sha,
        "source_event_id": event_id,
        "occurrence_index": occurrence_index,
        "immutable_source_key": cov_source_key(sha, event_id,
                                               occurrence_index),
        "entity_normalized": name or entity_normalized,
        "role_type": role_type,
        "canonical_person_id": canonical_person_id,
    }


def _frozen(tmp_path, records):
    path = tmp_path / "h1_frozen.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    return str(path)


def _link(conn, run, role_id):
    return conn.execute(
        "SELECT link_status, canonical_entity_id, match_metadata "
        "FROM registry_projection.entity_link "
        "WHERE projection_run_id=%s AND person_role_id=%s",
        (run, role_id)).fetchone()


def _build(conn, dsn, run, path):
    with _canon(dsn) as qc:
        stats = el.build_entity_links(run, projection_conn=conn,
                                      canonical_conn=qc,
                                      cov_frozen_path=path)
        conn.commit()
        qc.rollback()
    return stats


# --- the fallback ------------------------------------------------------------

def test_shifted_index_links_via_name_fallback(conn, dsn, tmp_path):
    run = _shadow(conn, "x3a-shift")
    role = _roles(conn, run, C_SHIFT)[0]
    assert role[2] == "a shift"
    rec = _record(conn, role, occurrence_index=3,
                  canonical_person_id=P_SHIFT)
    stats = _build(conn, dsn, run, _frozen(tmp_path, [rec]))

    assert stats["COV_HISTORICAL_FALLBACK_LINKED"] == 1
    assert stats["COV_HISTORICAL_ATTEMPTED"] == 1
    assert stats["COV_HISTORICAL_LINKED"] == 1
    status, canonical_id, meta = _link(conn, run, role[0])
    assert (status, canonical_id) == ("LINKED", P_SHIFT)
    assert meta["cov_match"] == "NAME_FALLBACK"
    assert meta["immutable_source_key"] == rec["immutable_source_key"]
    assert meta["occurrence_index"] == 3


def test_positional_match_is_unchanged(conn, dsn, tmp_path):
    run = _shadow(conn, "x3a-pos")
    pos = _roles(conn, run, C_POS)[0]
    shift = _roles(conn, run, C_SHIFT)[0]
    records = [
        _record(conn, pos, occurrence_index=0, canonical_person_id=P_POS),
        # positive control: the fallback must actually be live in this build
        _record(conn, shift, occurrence_index=3,
                canonical_person_id=P_SHIFT),
    ]
    stats = _build(conn, dsn, run, _frozen(tmp_path, records))

    assert stats["COV_HISTORICAL_LINKED"] == 2
    assert stats["COV_HISTORICAL_FALLBACK_LINKED"] == 1
    status, canonical_id, meta = _link(conn, run, pos[0])
    assert (status, canonical_id) == ("LINKED", P_POS)
    assert meta["cov_match"] == "POSITIONAL"
    assert meta["occurrence_index"] == 0
    s_status, s_canonical, s_meta = _link(conn, run, shift[0])
    assert (s_status, s_canonical) == ("LINKED", P_SHIFT)
    assert s_meta["cov_match"] == "NAME_FALLBACK"


def test_same_name_twice_is_ambiguous(conn, dsn, tmp_path):
    run = _shadow(conn, "x3a-dup")
    roles = _roles(conn, run, C_DUP)
    assert len(roles) == 2
    assert {r[2] for r in roles} == {"b dup", "b  dup"}
    records = [
        _record(conn, roles[0], occurrence_index=2,
                canonical_person_id=P_DUP1, name="b dup"),
        _record(conn, roles[0], occurrence_index=3,
                canonical_person_id=P_DUP2, name="b dup"),
    ]
    stats = _build(conn, dsn, run, _frozen(tmp_path, records))

    assert stats["COV_HISTORICAL_AMBIGUOUS"] == 2
    assert stats["COV_HISTORICAL_LINKED"] == 0
    assert stats["COV_HISTORICAL_FALLBACK_LINKED"] == 0
    for role in roles:
        status, canonical_id, _ = _link(conn, run, role[0])
        assert (status, canonical_id) == ("UNRESOLVED", None)


def test_different_name_at_shifted_position_is_not_linked(conn, dsn, tmp_path):
    run = _shadow(conn, "x3a-diff")
    roles = {r[2]: r for r in _roles(conn, run, C_DIFF)}
    role, good = roles["c diff"], roles["g good"]
    records = [
        _record(conn, role, occurrence_index=3,
                canonical_person_id=P_DUP1, name="d other"),
        # positive control: one matching shifted record must still link
        _record(conn, good, occurrence_index=2,
                canonical_person_id=P_GOOD),
    ]
    stats = _build(conn, dsn, run, _frozen(tmp_path, records))

    assert stats["COV_HISTORICAL_LINKED"] == 1
    assert stats["COV_HISTORICAL_FALLBACK_LINKED"] == 1
    assert stats["COV_HISTORICAL_OUTSIDE_H1"] >= 1
    status, canonical_id, _ = _link(conn, run, role[0])
    assert (status, canonical_id) == ("UNRESOLVED", None)
    g_status, g_canonical, g_meta = _link(conn, run, good[0])
    assert (g_status, g_canonical) == ("LINKED", P_GOOD)
    assert g_meta["cov_match"] == "NAME_FALLBACK"


def test_consecutive_shifts_link_each_own_record_once(conn, dsn, tmp_path):
    run = _shadow(conn, "x3a-pair")
    roles = {r[2]: r for r in _roles(conn, run, C_PAIR)}
    assert set(roles) == {"d one", "d two"}
    one, two = roles["d one"], roles["d two"]
    # parser v2 removed one pseudo change before both persons: frozen 1/2,
    # current 0/1. The second positional key lands on the first record.
    rec_one = _record(conn, one, occurrence_index=1,
                      canonical_person_id=P_PAIR1)
    rec_two = _record(conn, two, occurrence_index=2,
                      canonical_person_id=P_PAIR2)
    stats = _build(conn, dsn, run, _frozen(tmp_path, [rec_one, rec_two]))

    assert stats["COV_HISTORICAL_LINKED"] == 2
    assert stats["COV_HISTORICAL_FALLBACK_LINKED"] == 2
    assert stats["COV_HISTORICAL_AMBIGUOUS"] == 0
    one_status, one_canonical, one_meta = _link(conn, run, one[0])
    two_status, two_canonical, two_meta = _link(conn, run, two[0])
    assert (one_status, one_canonical) == ("LINKED", P_PAIR1)
    assert (two_status, two_canonical) == ("LINKED", P_PAIR2)
    assert one_meta["cov_match"] == "NAME_FALLBACK"
    assert two_meta["cov_match"] == "NAME_FALLBACK"
    assert one_meta["immutable_source_key"] == rec_one["immutable_source_key"]
    assert two_meta["immutable_source_key"] == rec_two["immutable_source_key"]


def test_positional_name_mismatch_without_fallback_is_not_linked(
        conn, dsn, tmp_path):
    run = _shadow(conn, "x3a-pos-mismatch")
    role = {r[2]: r for r in _roles(conn, run, C_DIFF)}["c diff"]
    # The role's positional key hits this record, but its frozen name is
    # another person's and the name index has no candidate for the role.
    rec = _record(conn, role, occurrence_index=0,
                  canonical_person_id=P_DUP1, name="d other")
    stats = _build(conn, dsn, run, _frozen(tmp_path, [rec]))

    assert stats["COV_HISTORICAL_LINKED"] == 0
    assert stats["COV_HISTORICAL_FALLBACK_LINKED"] == 0
    assert stats["COV_HISTORICAL_OUTSIDE_H1"] >= 1
    status, canonical_id, _ = _link(conn, run, role[0])
    assert (status, canonical_id) == ("UNRESOLVED", None)


def test_load_cov_frozen_skips_unusable_name_index(tmp_path):
    records = [
        {"document_sha256": "sha-a", "source_event_id": "not-an-int",
         "occurrence_index": 0, "immutable_source_key": "k-bad-event",
         "entity_normalized": "bad event", "role_type": "BOARD",
         "canonical_person_id": 1},
        {"document_sha256": "sha-b", "source_event_id": 7,
         "occurrence_index": 0, "immutable_source_key": "k-bad-name",
         "entity_normalized": 123, "role_type": "BOARD",
         "canonical_person_id": 2},
        {"document_sha256": "sha-c", "source_event_id": 8,
         "occurrence_index": 0, "immutable_source_key": "k-good",
         "entity_normalized": "Good Name", "role_type": "BOARD",
         "canonical_person_id": 3},
    ]
    by_key, by_name = el._load_cov_frozen(_frozen(tmp_path, records))

    assert set(by_key) == {"k-bad-event", "k-bad-name", "k-good"}
    assert by_name == {("sha-c", 8, "good name", "BOARD"): [records[2]]}


def test_already_linked_record_is_not_reused_by_fallback(conn, dsn, tmp_path):
    run = _shadow(conn, "x3a-used")
    role = _roles(conn, run, C_USED)[0]
    # A second role with the same normalised name and no parse result: its
    # positional key is absent, so it reaches the name fallback.
    dup_role_id = conn.execute(
        "INSERT INTO registry_projection.company_person_role "
        "(projection_run_id, company_id, role_type, person_name, "
        " entity_normalized, source_scope, source_event_id, "
        " source_document_id, source_observation_id, source_parse_result_id) "
        "VALUES (%s,%s,'BOARD','E  Used','e  used','HISTORICAL_EVENT',%s,%s,"
        "        %s,NULL) RETURNING id",
        (run, C_USED, role[3], role[4], role[5])).fetchone()[0]
    conn.commit()
    rec = _record(conn, role, occurrence_index=0,
                  canonical_person_id=P_USED)
    stats = _build(conn, dsn, run, _frozen(tmp_path, [rec]))

    assert stats["COV_HISTORICAL_LINKED"] == 1          # positional, once
    assert stats["COV_HISTORICAL_FALLBACK_LINKED"] == 0
    assert stats["COV_HISTORICAL_ATTEMPTED"] == 2
    assert stats["COV_HISTORICAL_AMBIGUOUS"] == 1       # reused record
    assert _link(conn, run, role[0])[:2] == ("LINKED", P_USED)
    assert _link(conn, run, dup_role_id)[:2] == ("UNRESOLVED", None)


def test_positional_hit_on_used_record_is_ambiguous(conn, dsn, tmp_path):
    run = _shadow(conn, "x3a-used-pos")
    role = _roles(conn, run, C_USED)[0]
    # Same parse result and name as the first role (whitespace variant keeps
    # the projection uniqueness rule happy): both compute the same positional
    # key, so the second positional claimant must not link the record again.
    dup_role_id = conn.execute(
        "INSERT INTO registry_projection.company_person_role "
        "(projection_run_id, company_id, role_type, person_name, "
        " entity_normalized, source_scope, source_event_id, "
        " source_document_id, source_observation_id, source_parse_result_id) "
        "VALUES (%s,%s,'BOARD','E  Used','e  used','HISTORICAL_EVENT',%s,%s,"
        "        %s,%s) RETURNING id",
        (run, C_USED, role[3], role[4], role[5], role[6])).fetchone()[0]
    conn.commit()
    rec = _record(conn, role, occurrence_index=0,
                  canonical_person_id=P_USED)
    stats = _build(conn, dsn, run, _frozen(tmp_path, [rec]))

    assert stats["COV_HISTORICAL_LINKED"] == 1
    assert stats["COV_HISTORICAL_ATTEMPTED"] == 2
    assert stats["COV_HISTORICAL_AMBIGUOUS"] == 1       # reused record
    assert _link(conn, run, role[0])[:2] == ("LINKED", P_USED)
    assert _link(conn, run, dup_role_id)[:2] == ("UNRESOLVED", None)


def test_fallback_stat_counts_in_one_build(conn, dsn, tmp_path):
    run = _shadow(conn, "x3a-stats")
    shift = _roles(conn, run, C_SHIFT)[0]
    pos = _roles(conn, run, C_POS)[0]
    records = [
        _record(conn, shift, occurrence_index=3,
                canonical_person_id=P_SHIFT),
        _record(conn, pos, occurrence_index=0,
                canonical_person_id=P_POS),
    ]
    stats = _build(conn, dsn, run, _frozen(tmp_path, records))

    assert stats["COV_HISTORICAL_ATTEMPTED"] == 2
    assert stats["COV_HISTORICAL_LINKED"] == 2
    assert stats["COV_HISTORICAL_FALLBACK_LINKED"] == 1
    assert _link(conn, run, shift[0])[2]["cov_match"] == "NAME_FALLBACK"
    assert _link(conn, run, pos[0])[2]["cov_match"] == "POSITIONAL"


@pytest.mark.parametrize("frozen_name,canonical_id", [
    # Synthetic stand-ins for REVIEW's three stale H1 shapes: two share only
    # a first name, one shares only a surname. The real names stay out of git
    # (I-5); the token-sharing shape is what the guard has to refuse.
    ("Zzone Alfa Testdam", 8201),       # first token only
    ("Zztwo Beta Testdam", 8202),       # first token only
    ("Zzthree Gamma Testsonne", 8203),  # last token only
])
def test_strict_target_guard_requires_first_and_last_name_token(
        conn, dsn, tmp_path, frozen_name, canonical_id):
    """Fix round 1 #2: one shared token is not enough.

    The three known-stale H1 shapes share only a first name or a surname
    with the canonical person. The strict guard requires both the frozen
    name's first and last normalised token, so each record stays fail-closed.
    """
    run = _shadow(conn, "x5b-strict-target")
    base = _roles(conn, run, C_DIFF)[0]
    _, _, _, event_id, doc_id, observation_id, parse_id = base
    norm = el.normalize_person_name(frozen_name)
    role_id = conn.execute(
        "INSERT INTO registry_projection.company_person_role "
        "(projection_run_id, company_id, role_type, person_name, "
        " entity_normalized, source_scope, source_document_id, "
        " source_observation_id, source_parse_result_id, source_event_id) "
        "VALUES (%s,%s,'BOARD',%s,%s,'HISTORICAL_EVENT',%s,%s,%s,%s) "
        "RETURNING id",
        (run, C_DIFF, frozen_name, norm, doc_id, observation_id, parse_id,
         event_id)).fetchone()[0]
    conn.commit()
    role = (role_id, "BOARD", norm, event_id, doc_id, observation_id, parse_id)
    rec = _record(conn, role, occurrence_index=0,
                  canonical_person_id=canonical_id, name=frozen_name)
    stats = _build(conn, dsn, run, _frozen(tmp_path, [rec]))

    assert stats["COV_HISTORICAL_TARGET_NAME_MISMATCH"] == 1
    assert stats["COV_HISTORICAL_LINKED"] == 0
    assert _link(conn, run, role_id)[:2] == ("UNRESOLVED", None)


def test_stale_canonical_target_name_mismatch_fails_closed(
        conn, dsn, tmp_path):
    """X5b review #6: a frozen record must name the canonical target it links.

    The positional key and frozen name match the role, so the source-side
    guard passes; the frozen canonical_person_id points at a person whose
    display name shares no token ('p pos' vs 'A Shift'). The link must fail
    closed and be counted, never produce a wrong person link."""
    run = _shadow(conn, "x5b-stale-target")
    role = _roles(conn, run, C_POS)[0]
    assert role[2] == "p pos"
    rec = _record(conn, role, occurrence_index=0,
                  canonical_person_id=P_SHIFT)
    stats = _build(conn, dsn, run, _frozen(tmp_path, [rec]))

    assert stats["COV_HISTORICAL_TARGET_NAME_MISMATCH"] == 1
    assert stats["COV_HISTORICAL_LINKED"] == 0
    assert _link(conn, run, role[0])[:2] == ("UNRESOLVED", None)
