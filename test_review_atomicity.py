"""Atomicity proofs for migrated review write paths (P0.2b).

- Injected failure mid-sequence leaves zero partial rows (rollback).
- Freeze -> dry-run -> apply E2E on throwaway fixtures.
- Pin stability: identical state reproduces the identical pin.
Runs against pdfhist_test only.
"""
from __future__ import annotations

import psycopg
import pytest

from heimdal.modules.review import repository as repo
from heimdal.modules.shared import db as shared_db
from heimdal.pdfhist.testsupport import ensure_source_event, ensure_test_database


@pytest.fixture(scope="module")
def dsn():
    return ensure_test_database()


@pytest.fixture()
def tconn(dsn):
    conn = psycopg.connect(dsn)
    conn.autocommit = True
    repo.apply_review_migration(conn)
    yield conn
    conn.execute("DELETE FROM registry_projection.person_review_decision "
                 "WHERE case_key LIKE 'ATOM|%'")
    conn.execute("DELETE FROM registry_projection.person_review_case "
                 "WHERE case_key LIKE 'ATOM|%'")
    conn.close()


def _tables():
    return ("registry_projection.projection_run",
            "registry_projection.entity_link")


def test_injected_failure_leaves_zero_partial_rows(dsn, tconn):
    with pytest.raises(RuntimeError):
        with shared_db.tx(dsn) as c:
            c.execute(
                "INSERT INTO registry_projection.person_review_case "
                "(case_key, source_kind, norm_name, source_name_exact) "
                "VALUES ('ATOM|1','PDF-HIST','a','A')")
            repo.materialize_packet(c, case_key="ATOM|1", packet={"x": 1})
            repo.insert_decision(c, case_key="ATOM|1", reviewer="t",
                                 decision="DEFER",
                                 reason_code="NO_CANONICAL_CANDIDATE",
                                 shown_packet_hash="h", shown_alternatives=[],
                                 shown_score_snapshot_ref="v", evidence_pin="e")
            raise RuntimeError("injected mid-sequence")
    assert tconn.execute(
        "SELECT count(*) FROM registry_projection.person_review_case "
        "WHERE case_key='ATOM|1'").fetchone()[0] == 0
    assert tconn.execute(
        "SELECT count(*) FROM registry_projection.person_review_decision "
        "WHERE case_key='ATOM|1'").fetchone()[0] == 0


def test_freeze_dryrun_apply_e2e(dsn, tconn):
    from heimdal.modules.review import service as svc
    from heimdal.pdfhist import human_links
    ensure_source_event(tconn, 7)
    with shared_db.tx(dsn) as c:
        run = c.execute(
            "INSERT INTO registry_projection.projection_run (projection_version, status, code_version) "
            "VALUES ('v-test', 'SHADOW', 't') RETURNING projection_run_id").fetchone()
        run_id = run["projection_run_id"] if isinstance(run, dict) else run[0]
        c.execute(
            "INSERT INTO registry_projection.company_person_role (id, "
            "projection_run_id, company_id, person_name, entity_normalized, "
            "role_type, source_scope, source_event_id) VALUES "
            "(601,%s,7,'Atom B','atom b','BOARD','HISTORICAL_EVENT',7)",
            (run_id,))
        c.execute(
            "INSERT INTO registry_projection.entity_link (projection_run_id, "
            "entity_type, source_entity_key, person_role_id, link_status, "
            "link_method, link_code_version) VALUES "
            "(%s,'PERSON','ROLE:601',601,'UNRESOLVED','EXACT_MULTI_ATTRIBUTE','v')",
            (run_id,))
        c.execute(
            "INSERT INTO registry_projection.person_review_case "
            "(case_key, source_kind, norm_name, source_name_exact) "
            "VALUES ('PDF-HIST|d|7|0|atom b|','PDF-HIST','atom b','Atom B')")
        did = repo.insert_decision(
            c, case_key="PDF-HIST|d|7|0|atom b|", reviewer="t",
            decision="APPROVE_LINK",
            chosen_canonical_id=7412, shown_packet_hash="h",
            shown_alternatives=[7412], shown_score_snapshot_ref="v1",
            evidence_pin="e")
        repo.attach_batch(c, did, "ATOM-batch")
    try:
        class FakeCanon:
            def execute(self, *a, **k):
                class R:
                    def fetchone(self): return ("PERSON",)
                return R()
        with shared_db.tx(dsn) as c:
            prev = svc.dry_run_preview(c, FakeCanon(), run_id, "ATOM-batch")
            assert prev["summary"]["clean_applicable"] == 1
            stats = human_links.apply_approved_human_links(c, run_id, [{
                "case_key": "PDF-HIST|d|7|0|atom b|",
                "source_stable_key": "PDF-HIST|d|7|0|atom b|",
                "source_entity_key": "ROLE:601", "canonical_entity_id": 7412,
                "decision_id": did, "decided_at": "2026-01-01",
                "decided_by": "t", "evidence_pin": "e",
                "shown_packet_hash": "h", "score_snapshot_ref": "v1",
                "owner_note": None, "owner_reason": None}], FakeCanon())
            assert stats["applied"] == 1
            repo.mark_applied(c, did, run_id)
        row = tconn.execute(
            "SELECT link_status, link_method, application_status "
            "FROM registry_projection.entity_link, "
            "registry_projection.person_review_decision "
            "WHERE projection_run_id=%s AND source_entity_key='ROLE:601' "
            "AND decision_id=%s", (run_id, did)).fetchone()
        vals = tuple(row) if not isinstance(row, dict) else tuple(row.values())
        assert vals == ("LINKED", "HUMAN-REVIEW-PERSON-v1", "APPLIED")
    finally:
        tconn.execute("DELETE FROM registry_projection.entity_link "
                      "WHERE projection_run_id=%s", (run_id,))
        tconn.execute("DELETE FROM registry_projection.company_person_role "
                      "WHERE projection_run_id=%s", (run_id,))
        tconn.execute("DELETE FROM registry_projection.projection_run "
                      "WHERE projection_run_id=%s", (run_id,))


def test_pin_stable_on_identical_state(dsn, tconn):
    from heimdal.modules.review import service as svc
    with shared_db.tx(dsn) as c:
        run = c.execute(
            "INSERT INTO registry_projection.projection_run (projection_version, status, code_version) "
            "VALUES ('v-test', 'SHADOW', 't') RETURNING projection_run_id").fetchone()
        run_id = run["projection_run_id"] if isinstance(run, dict) else run[0]
        c.execute(
            "INSERT INTO registry_projection.person_review_case "
            "(case_key, source_kind, norm_name, source_name_exact) "
            "VALUES ('ATOM|3','PDF-HIST','c','C')")
        did = repo.insert_decision(
            c, case_key="ATOM|3", reviewer="t", decision="APPROVE_LINK",
            chosen_canonical_id=7412, shown_packet_hash="h",
            shown_alternatives=[7412], shown_score_snapshot_ref="v1",
            evidence_pin="e")
        repo.attach_batch(c, did, "ATOM-batch-3")
        p1 = svc.batch_state_pin(c, run_id, [{
            "decision_id": did, "case_key": "ATOM|3",
            "chosen_canonical_id": 7412, "shown_packet_hash": "h"}])
        p2 = svc.batch_state_pin(c, run_id, [{
            "decision_id": did, "case_key": "ATOM|3",
            "chosen_canonical_id": 7412, "shown_packet_hash": "h"}])
        assert p1 == p2
    tconn.execute("DELETE FROM registry_projection.projection_run "
                  "WHERE projection_run_id=%s", (run_id,))
