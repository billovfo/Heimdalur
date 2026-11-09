"""Projection integrity regressions (audit 2026-11 lane W4d).

D-07  activate() refuses a SHADOW that fails validate(): broken provenance,
      authority-policy violation, or content that no longer matches its hash
D-08  entity links: unresolvable ownership pin and missing registry_ownership
      fail the build; a frozen COV target that is no canonical person stays
      UNRESOLVED; the frozen-map path comes from the caller
D-09  delete_non_active() removes a run that has entity links
D-10  a single-run build picks the NEWEST Felag snapshot of the run

Isolated: the throwaway HEIMDAL_TEST_DB only. The canonical side for the
entity-link build is a minimal schema "canon" inside the same test DB.
"""
import json

import psycopg
import pytest
from psycopg.types.json import Json

from heimdal.pdfhist import entity_links as el
from heimdal.pdfhist import projection
from heimdal.pdfhist.cov_historical import cov_source_key
from heimdal.pdfhist.testsupport import ensure_test_database
from test_registry_projection import RUN, _content, _seed_evidence

LINK_CO, LINK_REGNR = 9601, "9601"
BOARD = [{"type": "BOARD_CHANGE", "action": "APPOINTED", "entity": "Jón Test",
          "entity_normalized": "jón test",
          "raw_text": "Innkomin í nevndina: Jón Test, Vegur 1, 100 Tórshavn",
          "registration_date": "2021-06-01"}]


@pytest.fixture(scope="module")
def dsn():
    dsn = ensure_test_database()
    with psycopg.connect(dsn) as c:
        projection.apply_migration(c)
        _seed_evidence(c, 9501, "9501")
        _seed_evidence(c, LINK_CO, LINK_REGNR, BOARD)
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
            INSERT INTO canon.company_inventory VALUES ('S9601', 501);
            INSERT INTO canon.canonical_entity VALUES
                (1001, 501, 'COMPANY', 'P/F Linkfelag');
            INSERT INTO registry_ownership.build_meta VALUES
                (1, 'phase1-v1', 0, 0, 0, 0, 'h', 'ACTIVE');
            """)
    return dsn


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn) as c:
        yield c


def _shadow(conn, version):
    run = projection.build_shadow(conn, source_collection_run_id=RUN,
                                  projection_version=version)
    conn.commit()
    return run


def _status(conn, run):
    return conn.execute(
        "SELECT status FROM registry_projection.projection_run "
        "WHERE projection_run_id=%s", (run,)).fetchone()[0]


# --- D-07 --------------------------------------------------------------------

def test_activate_refuses_the_drill_shadow(conn):
    """The D report drill: broken provenance + an auditor promoted to
    current state. validate() was advisory only; activate() must refuse."""
    run = _shadow(conn, "w4d-drill")
    conn.execute(
        "UPDATE registry_projection.company_current_state_field SET "
        "source_document_id=NULL WHERE projection_run_id=%s AND "
        "authority_state='AUTHORITATIVE_SNAPSHOT'", (run,))
    conn.execute(
        "INSERT INTO registry_projection.company_current_state_field "
        "(projection_run_id, company_id, field_name, value_json, "
        "authority_state) VALUES (%s, 9501, 'auditor', %s, "
        "'AUTHORITATIVE_SNAPSHOT')", (run, Json("X Grannskoðari")))
    conn.commit()
    with pytest.raises(ValueError) as exc:
        projection.activate(conn, run)
    conn.rollback()
    msg = str(exc.value)
    for blocker in ("broken_provenance_refs", "authority_policy_violations",
                    "content_hash"):
        assert blocker in msg
    assert _status(conn, run) == "SHADOW"
    assert projection.active_run_id(conn) != run


def test_activate_refuses_content_that_no_longer_matches_its_hash(conn):
    run = _shadow(conn, "w4d-tamper")
    conn.execute(
        "UPDATE registry_projection.company_current_state_field SET "
        "value_json=%s WHERE projection_run_id=%s AND "
        "field_name='company_name'", (Json("P/F Onnur"), run))
    conn.commit()
    assert projection.validate(conn, run)["content_hash_matches"] is False
    with pytest.raises(ValueError, match="content_hash"):
        projection.activate(conn, run)
    conn.rollback()
    assert _status(conn, run) == "SHADOW"


def test_clean_shadow_activates(conn):
    run = _shadow(conn, "w4d-clean")
    v = projection.validate(conn, run)
    assert v["content_hash_matches"] is True
    assert not any(v[k] for k in projection.ACTIVATION_BLOCKERS)
    projection.activate(conn, run)
    conn.commit()
    assert projection.active_run_id(conn) == run
    projection.retire(conn, run)
    conn.commit()


# --- D-08 / D-09 -------------------------------------------------------------

def _canon(dsn):
    return psycopg.connect(dsn, options="-c search_path=canon,public")


def _frozen(tmp_path, conn, run, canonical_person_id):
    role, sha, sd, person_name = conn.execute(
        "SELECT pr.source_event_id, o.sha256, p.structured_data, "
        "pr.person_name FROM "
        "registry_projection.company_person_role pr JOIN "
        "pdf_document_observation o ON o.id = pr.source_observation_id JOIN "
        "pdf_parse_result p ON p.id = pr.source_parse_result_id WHERE "
        "pr.projection_run_id=%s AND pr.company_id=%s AND "
        "pr.source_scope='HISTORICAL_EVENT'", (run, LINK_CO)).fetchone()
    occ = next(i for i, ch in enumerate(sd["changes"])
               if ch["entity_normalized"] == "jón test")
    path = tmp_path / "h1_frozen.json"
    path.write_text(json.dumps([{
        "immutable_source_key": cov_source_key(sha, role, occ),
        "person_name": person_name,
        "canonical_person_id": canonical_person_id}]))
    return str(path)


def _person_link(conn, run):
    return conn.execute(
        "SELECT l.link_status, l.canonical_entity_id FROM "
        "registry_projection.entity_link l JOIN "
        "registry_projection.company_person_role pr ON pr.id=l.person_role_id"
        " WHERE l.projection_run_id=%s AND pr.company_id=%s AND "
        "pr.source_scope='HISTORICAL_EVENT'", (run, LINK_CO)).fetchone()


def test_entity_links_fail_closed_and_cov_guard(dsn, conn, tmp_path):
    run = _shadow(conn, "w4d-links")
    with _canon(dsn) as qc:
        for pin in ("own-build:99", "build-1"):  # unknown / malformed pin
            with pytest.raises((RuntimeError, ValueError)):
                el.build_entity_links(run, projection_conn=conn,
                                      canonical_conn=qc,
                                      ownership_generation_pin=pin)
            conn.rollback()
        # a missing registry_ownership is a failed build, not an empty map
        qc.execute("DROP SCHEMA registry_ownership CASCADE")
        with pytest.raises(psycopg.errors.UndefinedTable):
            el.build_entity_links(run, projection_conn=conn, canonical_conn=qc)
        qc.rollback()
        conn.rollback()
        # COV target 777 is no canonical PERSON: never a link
        path = _frozen(tmp_path, conn, run, 777)
        stats = el.build_entity_links(run, projection_conn=conn,
                                      canonical_conn=qc, cov_frozen_path=path)
        conn.commit()
        assert stats["COV_HISTORICAL_FROZEN_PATH"] == path
        assert stats["COV_HISTORICAL_AMBIGUOUS"] == 1
        assert _person_link(conn, run) == ("UNRESOLVED", None)
        qc.execute("INSERT INTO canon.canonical_entity VALUES "
                   "(777, 777, 'PERSON', 'Jón Test')")
        stats = el.build_entity_links(run, projection_conn=conn,
                                      canonical_conn=qc, cov_frozen_path=path)
        conn.commit()
        qc.rollback()
    assert stats["COV_HISTORICAL_LINKED"] == 1
    assert _person_link(conn, run) == ("LINKED", 777)
    # D-09: a run with entity links can be deleted
    projection.delete_non_active(conn, run)
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM registry_projection.entity_link WHERE "
        "projection_run_id=%s", (run,)).fetchone()[0] == 0


def test_cov_tier_off_without_a_frozen_path(monkeypatch):
    monkeypatch.delenv("HEIMDAL_COV_H1_FROZEN", raising=False)
    assert el._load_cov_historical_frozen(None) == {}
    with pytest.raises(FileNotFoundError):
        el._load_cov_historical_frozen("/nonexistent/h1_frozen.json")


# --- D-10 --------------------------------------------------------------------

def test_single_run_build_picks_the_newest_felag_snapshot(conn):
    """Two Felag snapshots of one company in one run: the newest wins."""
    company = 9701
    _seed_evidence(conn, company, "9701", snapshot_date="2026-08-30")
    doc = conn.execute(
        "INSERT INTO pdf_document (pdf_type, source_document_id) VALUES "
        "('FelagPdf', 'F9701-new') RETURNING id").fetchone()[0]
    obs = conn.execute(
        "INSERT INTO pdf_document_observation (document_id, "
        "collection_run_id, fetched_at, document_snapshot_date, sha256, "
        "byte_length, extracted_text) VALUES (%s,%s,now(),'2026-10-01',%s,1,"
        "'snap') RETURNING id",
        (doc, RUN, _content(conn, "felag-9701-new"))).fetchone()[0]
    conn.execute(
        "INSERT INTO pdf_parse_result (document_observation_id, "
        "parser_version, document_kind, raw_text, structured_data, "
        "parse_status) VALUES (%s,'1','COMPANY_SNAPSHOT','snap',%s,'PARSED')",
        (obs, Json({"registration_number": "9701"})))
    conn.execute(
        "INSERT INTO pdf_source_event_document (source_event_id, document_id,"
        " cell_role) SELECT id, %s, 'FELAG_CELL' FROM pdf_source_event "
        "WHERE company_id=%s", (doc, company))
    picked = [r for r in projection._latest_snapshots(conn, RUN)
              if r[0] == company]
    conn.rollback()
    assert [str(r[1]) for r in picked] == ["2026-10-01"]
