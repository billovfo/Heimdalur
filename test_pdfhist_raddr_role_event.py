"""R-ADDR reads the role's own membership event text (audit 2026-11 lane X3c).

Since X3b one notice (one ``source_event_id``) can hold several membership
events: a new liquidator appointment ahead of the director's removal, for
example. R-ADDR used to read only the notice's first ``company_history_event``
raw text, so the second person's name was not found and the role got no
address. R-ADDR must look up the text of the role's own event by
``(source_event_id, entity_normalized as stored)`` and use the first-event
text only when that row is missing.

Isolated: throwaway HEIMDAL_TEST_DB only; the canonical side is a minimal
schema ``canon`` and ``registry_ownership`` inside the same test DB. Synthetic
names and addresses only.
"""
import psycopg
import pytest

from heimdal.pdfhist import entity_links as el
from heimdal.pdfhist import projection
from heimdal.pdfhist.testsupport import ensure_test_database
from test_registry_projection import RUN, _seed_evidence

# --- synthetic companies / canonical persons (never real register values) ----

C_ROLE, R_ROLE = 9821, "9821"
C_FIRST, R_FIRST = 9822, "9822"
C_AMB, R_AMB = 9823, "9823"

P_ROLE = 8201
P_FIRST = 8202
P_AMB1, P_AMB2 = 8203, 8204

RAW_ROLE, RAW_FIRST = 7001, 7002
RAW_AMB1, RAW_AMB2 = 7003, 7004

# One notice, two membership events. The first event belongs to another
# person; only the second event carries the target person's address.
TWO_EVENT_CHANGES = [
    {"type": "BOARD_CHANGE", "action": "APPOINTED", "entity": "Other Person",
     "entity_normalized": "other person",
     "raw_text": "Innkomin: Other Person, Vegur 9",
     "registration_date": "2021-06-01"},
    {"type": "BOARD_CHANGE", "action": "APPOINTED", "entity": "Target Person",
     "entity_normalized": "target person",
     "raw_text": "Innkomin: Target Person, Vegur 7",
     "registration_date": "2021-06-02"},
]

# The only history event names another person first; the fallback role's name
# and address still appear later in the same first-event raw text.
FIRST_FALLBACK_CHANGES = [
    {"type": "BOARD_CHANGE", "action": "APPOINTED", "entity": "Other Person",
     "entity_normalized": "other person",
     "raw_text": "Innkomin: Other Person, First Fallback Person, Vegur 8",
     "registration_date": "2021-06-01"},
]

AMBIGUOUS_CHANGES = [
    {"type": "BOARD_CHANGE", "action": "APPOINTED", "entity": "Other Person",
     "entity_normalized": "other person",
     "raw_text": "Innkomin: Other Person, Vegur 9",
     "registration_date": "2021-06-01"},
    {"type": "BOARD_CHANGE", "action": "APPOINTED",
     "entity": "Ambiguous Person", "entity_normalized": "ambiguous person",
     "raw_text": "Innkomin: Ambiguous Person, Vegur 6",
     "registration_date": "2021-06-02"},
]


@pytest.fixture(scope="module")
def dsn():
    dsn = ensure_test_database()
    with psycopg.connect(dsn) as c:
        projection.apply_migration(c)
        _seed_evidence(c, C_ROLE, R_ROLE, TWO_EVENT_CHANGES)
        _seed_evidence(c, C_FIRST, R_FIRST, FIRST_FALLBACK_CHANGES)
        _seed_evidence(c, C_AMB, R_AMB, AMBIGUOUS_CHANGES)
        # Minimal canonical side + a pinned ownership generation. The raw
        # identity labels use the frozen source format 'Name - Persónur, addr'.
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
                (1, 'phase1-v1', 4, 7004, 0, 0, 'x3c', 'ACTIVE');
            INSERT INTO registry_ownership.relation VALUES
                (7001, 7001, 'Target Person - Persónur, Vegur 7'),
                (7002, 7002, 'First Fallback Person - Persónur, Vegur 8'),
                (7003, 7003, 'Ambiguous Person - Persónur, Vegur 6'),
                (7004, 7004, 'Ambiguous Person - Persónur, Vegur 6');
            INSERT INTO registry_ownership.relation_evidence_pointer VALUES
                (7001, 0, 0), (7002, 0, 0), (7003, 0, 0), (7004, 0, 0);
            INSERT INTO canon.company_inventory VALUES
                ('9821', 9001), ('9822', 9002), ('9823', 9003);
            INSERT INTO canon.canonical_entity VALUES
                (9001, 9001, 'COMPANY', 'C Role'),
                (9002, 9002, 'COMPANY', 'C First'),
                (9003, 9003, 'COMPANY', 'C Amb'),
                (8201, 7001, 'PERSON', 'Target Person'),
                (8202, 7002, 'PERSON', 'First Fallback Person'),
                (8203, 7003, 'PERSON', 'Ambiguous Person'),
                (8204, 7004, 'PERSON', 'Ambiguous Person');
            """)
        c.commit()
    return dsn


def _shadow(conn, version):
    run = projection.build_shadow(conn, source_collection_run_id=RUN,
                                  projection_version=version)
    conn.commit()
    return run


def _role(conn, run, company_id, entity_normalized):
    return conn.execute(
        "SELECT id, role_type, entity_normalized, source_event_id, "
        "source_document_id, source_observation_id, source_parse_result_id "
        "FROM registry_projection.company_person_role "
        "WHERE projection_run_id=%s AND company_id=%s "
        "  AND entity_normalized=%s AND source_scope='HISTORICAL_EVENT'",
        (run, company_id, entity_normalized)).fetchone()


def _build(conn, dsn, run):
    with psycopg.connect(dsn, options="-c search_path=canon,public") as qc:
        stats = el.build_entity_links(run, projection_conn=conn,
                                      canonical_conn=qc)
        conn.commit()
        qc.rollback()
    return stats


def _link(conn, run, role_id):
    return conn.execute(
        "SELECT link_status, canonical_entity_id, match_metadata "
        "FROM registry_projection.entity_link "
        "WHERE projection_run_id=%s AND person_role_id=%s",
        (run, role_id)).fetchone()


# --- the role's own event text -------------------------------------------------

def test_raddr_reads_the_roles_own_event_text(dsn):
    """The second membership event of a notice links via ROLE_EVENT."""
    with psycopg.connect(dsn) as conn:
        run = _shadow(conn, "x3c-role-event")
        first = _role(conn, run, C_ROLE, "other person")
        role = _role(conn, run, C_ROLE, "target person")
        assert first[0] < role[0]  # the other person's event really is first

        _build(conn, dsn, run)

        status, canonical_id, meta = _link(conn, run, role[0])
        assert (status, canonical_id) == ("LINKED", P_ROLE)
        assert meta["resolution_rule"] == "R-ADDR"
        assert meta["address_text_source"] == "ROLE_EVENT"
        assert meta["pdf_address_normalized"] == "vegur 7"
        # the first event's person must not borrow the second event's text
        assert _link(conn, run, first[0])[:2] == ("UNRESOLVED", None)


def test_raddr_falls_back_to_first_event_text(dsn):
    """Without a (source_event_id, entity_key) row, the first text is used."""
    with psycopg.connect(dsn) as conn:
        run = _shadow(conn, "x3c-first-event")
        first = _role(conn, run, C_FIRST, "other person")
        # A role of the same notice with no matching company_history_event row.
        role_id = conn.execute(
            "INSERT INTO registry_projection.company_person_role "
            "(projection_run_id, company_id, role_type, person_name, "
            " role_title, entity_normalized, source_scope, source_event_id, "
            " source_document_id, source_observation_id, source_parse_result_id) "
            "VALUES (%s,%s,'BOARD','First Fallback Person',NULL,"
            "        'first fallback person','HISTORICAL_EVENT',%s,%s,%s,%s) "
            "RETURNING id",
            (run, C_FIRST, first[3], first[4], first[5], first[6])).fetchone()[0]
        conn.commit()

        _build(conn, dsn, run)

        status, canonical_id, meta = _link(conn, run, role_id)
        assert (status, canonical_id) == ("LINKED", P_FIRST)
        assert meta["resolution_rule"] == "R-ADDR"
        assert meta["address_text_source"] == "FIRST_EVENT"
        assert meta["pdf_address_normalized"] == "vegur 8"


def test_raddr_ambiguity_stays_fail_closed(dsn):
    """Two raw identities on the role's own text stay AMBIGUOUS."""
    with psycopg.connect(dsn) as conn:
        run = _shadow(conn, "x3c-ambiguous")
        role = _role(conn, run, C_AMB, "ambiguous person")

        _build(conn, dsn, run)

        status, canonical_id, meta = _link(conn, run, role[0])
        assert (status, canonical_id) == ("AMBIGUOUS", None)
        assert meta["resolution_rule"] == "R-ADDR"
        assert meta["address_text_source"] == "ROLE_EVENT"
        assert meta["candidate_canonical_ids"] == [P_AMB1, P_AMB2]
