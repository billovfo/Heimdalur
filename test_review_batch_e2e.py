"""Person review over HTTP, end to end (audit 2026-11 W4a: E-1...E-14, D9, D10, D15).

decide -> freeze(target=SHADOW) -> dry-run -> apply through the Flask test
client, then a link rebuild of the SHADOW run (the D10 human tier), two
concurrent first decisions, the E-12 snapshot position and the E-14 trigger.
Isolated: the throwaway HEIMDAL_TEST_DB only (canonical tables seeded into it).
Synthetic names only.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time

import psycopg
from psycopg.rows import dict_row
import pytest

from heimdal.modules.review import repository as repo
from heimdal.pdfhist.testsupport import ensure_test_database

JS = {"Accept": "application/json"}
NAMES = ("anna test", "bo test", "ce test", "di test")


def _seed(c):
    from heimdal.pdfhist.projection_migrate import apply_projection_migration
    apply_projection_migration(c)
    repo.apply_review_migration(c)
    c.execute("CREATE TABLE canonical_entity (canonical_entity_id bigint PRIMARY KEY,"
              " entity_kind text, display_name text, source_entity_id bigint)")
    c.execute("CREATE TABLE company_inventory (registration_number text, source_entity_id bigint)")
    c.execute("CREATE TABLE v_role_relation (company_id bigint, person_name text,"
              " person_id bigint, inferred_group text)")
    c.execute("INSERT INTO canonical_entity VALUES (9001,'COMPANY','C',2),"
              " (7412,'PERSON','A',11), (7413,'PERSON','B1',12), (7414,'PERSON','B2',13),"
              " (7415,'PERSON','C',14), (7416,'PERSON','D',15), (7999,'PERSON','Z',16)")
    c.execute("INSERT INTO company_inventory VALUES ('S1', 2)")
    # W4d (D-08): entity links resolve a pinned ownership generation or fail
    # closed, so the seeded canonical side carries one ACTIVE build_meta row.
    c.execute("CREATE SCHEMA IF NOT EXISTS registry_ownership")
    c.execute("CREATE TABLE registry_ownership.build_meta (build_id bigint PRIMARY KEY,"
              " schema_version text, source_relation_count bigint, source_relation_max_id bigint,"
              " max_source_attempt_id bigint, max_collection_run_id bigint, content_hash text,"
              " status text)")
    c.execute("INSERT INTO registry_ownership.build_meta VALUES"
              " (1, 'phase1-v1', 0, 0, 0, 0, 'w4a-e2e', 'ACTIVE')")
    # Empty ownership tier: R-ADDR finds no address evidence, so only the
    # human tier decides these links (the subject of this test).
    c.execute("CREATE TABLE registry_ownership.relation (relation_id bigint PRIMARY KEY,"
              " raw_from_source_entity_id bigint, raw_from_source_entity_label text)")
    c.execute("CREATE TABLE registry_ownership.relation_evidence_pointer ("
              " relation_id bigint, source_attempt_id bigint, collection_run_id bigint)")
    # canonical data newer than the runs: every packet now shows candidates
    c.execute("INSERT INTO v_role_relation VALUES (9001,'Anna Test',7412,'NEVND'),"
              " (9001,'Bo Test',7413,'NEVND'), (9001,'Bo Test',7414,'NEVND'),"
              " (9001,'Ce Test',7415,'NEVND'), (9001,'Di Test',7416,'NEVND')")
    sha = hashlib.sha256(b"w4a").hexdigest()
    c.execute("INSERT INTO pdf_document_content VALUES (%s,1,'\\x00')", (sha,))
    doc = c.execute("INSERT INTO pdf_document (pdf_type, source_document_id)"
                    " VALUES ('X','w4a') RETURNING id").fetchone()[0]
    obs = c.execute("INSERT INTO pdf_document_observation (document_id, collection_run_id,"
                    " fetched_at, sha256, byte_length) VALUES (%s,1,now(),%s,1)"
                    " RETURNING id", (doc, sha)).fetchone()[0]
    par = c.execute("INSERT INTO pdf_parse_result (document_observation_id, raw_text,"
                    " structured_data, parse_status) VALUES (%s,'t',%s,'PARSED') RETURNING id",
                    (obs, json.dumps({"changes": [{"entity_normalized": n} for n in NAMES]}))
                    ).fetchone()[0]
    ev = c.execute("INSERT INTO pdf_source_event (company_id, requested_regnr, row_skrasnr,"
                   " source_row_key, raw_text, page_no, row_ordinal)"
                   " VALUES (1,'1','1','k','r',0,1) RETURNING id").fetchone()[0]
    runs, roles = {}, {}
    for status in ("ACTIVE", "SHADOW"):
        run = c.execute("INSERT INTO registry_projection.projection_run (projection_version,"
                        " status, code_version, content_hash) VALUES ('v',%s,'t',%s)"
                        " RETURNING projection_run_id", (status, status)).fetchone()[0]
        runs[status] = run
        c.execute("INSERT INTO registry_projection.company_current_state (projection_run_id,"
                  " company_id, registration_number) VALUES (%s,1,'1')", (run,))
        for n in NAMES:
            rid = c.execute(
                "INSERT INTO registry_projection.company_person_role (projection_run_id,"
                " company_id, role_type, person_name, entity_normalized, source_scope,"
                " source_document_id, source_observation_id, source_parse_result_id,"
                " source_event_id) VALUES (%s,1,'BOARD',%s,%s,'HISTORICAL_EVENT',%s,%s,%s,%s)"
                " RETURNING id", (run, n.title(), n, doc, obs, par, ev)).fetchone()[0]
            c.execute("INSERT INTO registry_projection.entity_link (projection_run_id,"
                      " entity_type, source_entity_key, person_role_id, link_status,"
                      " link_method, link_code_version) VALUES (%s,'PERSON',%s,%s,"
                      "'UNRESOLVED','EXACT_MULTI_ATTRIBUTE','v')", (run, "ROLE:%d" % rid, rid))
            roles[(status, n)] = rid
    # SHADOW: the automatic tier linked Ce Test to another person (conflict)
    c.execute("UPDATE registry_projection.entity_link SET link_status='LINKED',"
              " canonical_entity_id=7999 WHERE person_role_id=%s",
              (roles[("SHADOW", "ce test")],))
    # ACTIVE: Di Test is already linked automatically, so it is not decidable
    c.execute("UPDATE registry_projection.entity_link SET link_status='LINKED',"
              " canonical_entity_id=7416 WHERE person_role_id=%s",
              (roles[("ACTIVE", "di test")],))
    return runs, roles


@pytest.fixture(scope="module")
def env():
    dsn = ensure_test_database()
    c = psycopg.connect(dsn, autocommit=True)
    runs, roles = _seed(c)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HEIMDAL_REVIEW_UI", "1")
        mp.setenv("HEIMDAL_PDFHIST_PROJECTION_DSN", dsn)
        import heimdal.config
        mp.setattr(heimdal.config, "DB_URI", dsn)  # canonical reads: the seeded tables
        from heimdal.app import create_app
        app = create_app()
        app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        yield {"c": c, "dsn": dsn, "app": app, "runs": runs, "roles": roles}
    c.close()


def _decide(cl, role, decision, chosen=None, reason=None, accept=JS):
    url = "/persons/review/%d" % role
    h = cl.get(url, headers=JS).get_json()["packet_hash"]
    data = {"decision": decision, "shown_packet_hash": h}
    if chosen:
        data["chosen_canonical_id"] = str(chosen)
    if reason:
        data["reason_code"] = reason
    return cl.post(url + "/decision", data=data, headers=accept)


def _decision(c, did):
    return c.execute("SELECT application_status, stale_reason, applied_projection_run_id,"
                     " supersedes_decision_id FROM registry_projection.person_review_decision"
                     " WHERE decision_id=%s", (did,)).fetchone()


def _link(c, role):
    return c.execute("SELECT link_status, canonical_entity_id, link_method FROM"
                     " registry_projection.entity_link WHERE person_role_id=%s",
                     (role,)).fetchone()


def test_batch_flow_newest_decision_wins(env):
    c, roles, runs = env["c"], env["roles"], env["runs"]
    a, b, ce = (roles[("ACTIVE", n)] for n in ("anna test", "bo test", "ce test"))
    cl = env["app"].test_client()
    # E-10/E-9: an automatically LINKED role cannot be decided; no 500
    di = "/persons/review/%d" % roles[("ACTIVE", "di test")]
    assert cl.get(di, headers=JS).status_code == 409
    assert cl.post(di + "/decision", headers=JS, data={
        "decision": "DEFER", "reason_code": "OTHER_DEFER",
        "shown_packet_hash": "x"}).status_code == 409
    # E-4/D15: Bo's packet shows 7413 and 7414; anything else is refused
    assert _decide(cl, b, "APPROVE_LINK", 7412).status_code == 400
    d1 = _decide(cl, a, "APPROVE_LINK", 7412).get_json()["decision_id"]
    # E-2: a later REJECT on the same case supersedes the approval.
    # E-9: the HTML form redirects to the next queue item, not to id+1.
    r = _decide(cl, a, "REJECT", reason="WRONG_PERSON", accept={})
    assert r.status_code == 302 and r.headers["Location"].endswith("/%d" % b), r.headers
    d2 = c.execute("SELECT max(decision_id) FROM registry_projection.person_review_decision"
                   ).fetchone()[0]
    assert _decision(c, d1)[:2] == ("STALE", "SUPERSEDED")
    assert _decision(c, d2)[3] == d1
    d3 = _decide(cl, b, "APPROVE_LINK", 7413).get_json()["decision_id"]
    d4 = _decide(cl, ce, "APPROVE_LINK", 7415).get_json()["decision_id"]

    shadow = {"target_run_id": runs["SHADOW"]}
    fr = cl.post("/persons/review/batch/freeze", json=shadow).get_json()
    assert fr["clean"] == [d3] and fr["conflict"] == [d4] and fr["stale"] == [], fr
    dr = cl.post("/persons/review/batch/dry-run",
                 json={"frozen_batch_id": fr["frozen_batch_id"], **shadow}).get_json()
    assert dr["summary"]["clean_applicable"] == 1 and dr["target_run_id"] == runs["SHADOW"]
    r = cl.post("/persons/review/batch/apply", json={
        "frozen_batch_id": fr["frozen_batch_id"], "state_pin": dr["summary"]["state_pin"],
        **shadow})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["applied"] == 1 and r.get_json()["outcomes"] == {str(d3): "applied"}

    # E-3: every decision carries what happened to it
    assert _decision(c, d3)[:3] == ("APPLIED", None, runs["SHADOW"])
    assert _decision(c, d4)[:2] == ("CONFLICT", "TARGET-CONFLICT")
    assert _decision(c, d2)[0] == "PENDING"  # the live REJECT
    # the link is written only for the surviving approval, only in SHADOW
    s = {n: _link(c, roles[("SHADOW", n)]) for n in ("anna test", "bo test", "ce test")}
    assert s["bo test"] == ("LINKED", 7413, "HUMAN-REVIEW-PERSON-v1")
    assert s["anna test"][0] == "UNRESOLVED"
    assert s["ce test"][:2] == ("LINKED", 7999)  # automatic link untouched
    assert [_link(c, x)[0] for x in (a, b, ce)] == ["UNRESOLVED"] * 3

    # E-1: ACTIVE and missing targets are refused before any work
    assert cl.post("/persons/review/batch/freeze",
                   json={"target_run_id": runs["ACTIVE"]}).status_code == 409
    assert cl.post("/persons/review/batch/apply", json={
        "frozen_batch_id": fr["frozen_batch_id"], "state_pin": "x",
        "target_run_id": 999999}).status_code == 404

    # D10: rebuilding the SHADOW links keeps the applied human link. Bo is
    # AMBIGUOUS to the automatic tier, Anna and Ce link automatically.
    from heimdal.pdfhist import entity_links as el
    with psycopg.connect(env["dsn"], autocommit=True) as canon:
        stats = el.build_entity_links(runs["SHADOW"], projection_conn=c, canonical_conn=canon)
    assert stats["HUMAN_REVIEW"]["outcomes"] == {d3: "applied"}, stats.get("HUMAN_REVIEW")
    assert _link(c, roles[("SHADOW", "bo test")]) == ("LINKED", 7413, "HUMAN-REVIEW-PERSON-v1")
    assert _link(c, roles[("SHADOW", "anna test")])[:2] == ("LINKED", 7412)
    tier = c.execute("SELECT metrics_json->'human_review_tier'->>'applied' FROM"
                     " registry_projection.projection_run WHERE projection_run_id=%s",
                     (runs["SHADOW"],)).fetchone()[0]
    assert tier == "1"
    assert _decision(c, d3)[:3] == ("APPLIED", None, runs["SHADOW"])  # rows only read


def test_two_concurrent_first_decisions(env):
    """E-9/E-11: two first decisions on one new case: no 500, one case, a chain."""
    from heimdal.modules.review import service
    c, dsn = env["c"], env["dsn"]
    role = env["roles"][("ACTIVE", "ce test")]
    c.execute("DELETE FROM registry_projection.person_review_decision WHERE case_key IN"
              " (SELECT case_key FROM registry_projection.person_review_case"
              "  WHERE norm_name = 'ce test')")
    c.execute("DELETE FROM registry_projection.person_review_case WHERE norm_name='ce test'")
    with psycopg.connect(dsn) as canon:
        packet = service.build_packet(c, canon, env["runs"]["ACTIVE"], role)
    data = {"decision": "DEFER", "reason_code": "OTHER_DEFER",
            "shown_packet_hash": repo.packet_hash(packet)}
    codes = []

    def post():
        codes.append(env["app"].test_client().post(
            "/persons/review/%d/decision" % role, data=data, headers=JS).status_code)

    blocker = psycopg.connect(dsn)  # holds both inserts until both wait
    blocker.execute("LOCK TABLE registry_projection.person_review_case"
                    " IN SHARE ROW EXCLUSIVE MODE")
    threads = [threading.Thread(target=post) for _ in range(2)]
    for t in threads:
        t.start()
    for _ in range(200):
        n = c.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                      " AND wait_event_type = 'Lock'").fetchone()[0]
        if n >= 2:
            break
        time.sleep(0.05)
    blocker.commit()
    blocker.close()
    for t in threads:
        t.join(30)
    assert codes == [200, 200], codes
    rows = c.execute("SELECT decision_id, supersedes_decision_id, application_status FROM"
                     " registry_projection.person_review_decision d JOIN"
                     " registry_projection.person_review_case k USING (case_key)"
                     " WHERE k.norm_name = 'ce test' ORDER BY decision_id").fetchall()
    assert len(rows) == 2 and rows[1][1] == rows[0][0], rows
    assert rows[0][2] == "STALE" and rows[1][2] == "PENDING", rows
    assert c.execute("SELECT count(*) FROM registry_projection.person_review_case"
                     " WHERE norm_name = 'ce test'").fetchone()[0] == 1


def test_current_case_position_is_stable(env):
    """E-12: a PDF-CURRENT role's position comes from its own snapshot list."""
    from heimdal.modules.review import routes
    c = env["c"]
    obs, par = c.execute("SELECT document_observation_id, id FROM pdf_parse_result"
                         " LIMIT 1").fetchone()
    par2 = c.execute("INSERT INTO pdf_parse_result (document_observation_id, raw_text,"
                     " structured_data, parse_status, parser_version)"
                     " VALUES (%s,'t',%s,'PARSED','w4a-2') RETURNING id",
                     (obs, json.dumps({"board": [{"name": "Eva Test"}, {"name": "Finn Test"}]}))
                     ).fetchone()[0]
    pos = []
    for others in (0, 3):  # more roles earlier in the run must not shift it
        run = c.execute("INSERT INTO registry_projection.projection_run (projection_version,"
                        " status, code_version) VALUES ('v','SHADOW','t')"
                        " RETURNING projection_run_id").fetchone()[0]
        for i in range(others):
            c.execute("INSERT INTO registry_projection.company_person_role (projection_run_id,"
                      " company_id, role_type, person_name, entity_normalized, source_scope)"
                      " VALUES (%s,%s,'BOARD','X','x','CURRENT_AUTHORITATIVE')", (run, 50 + i))
        rid = c.execute(
            "INSERT INTO registry_projection.company_person_role (projection_run_id,"
            " company_id, role_type, person_name, entity_normalized, source_scope,"
            " source_observation_id, source_parse_result_id) VALUES"
            " (%s,2,'BOARD','Finn Test','finn test','CURRENT_AUTHORITATIVE',%s,%s)"
            " RETURNING id", (run, obs, par2)).fetchone()[0]
        with psycopg.connect(env["dsn"], row_factory=dict_row) as dc:
            pos.append(routes._persisted_snap_position(dc, run, rid))
    assert pos == [1, 1], pos


def test_payload_update_refused_by_trigger(env):
    """E-14: payload columns are INSERT-only in the DB itself."""
    c = env["c"]
    did = c.execute("SELECT min(decision_id) FROM registry_projection.person_review_decision"
                    ).fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException):
        c.execute("UPDATE registry_projection.person_review_decision"
                  " SET chosen_canonical_id = 1 WHERE decision_id = %s", (did,))
    c.execute("UPDATE registry_projection.person_review_decision"
              " SET qa_note = 'lifecycle ok' WHERE decision_id = %s", (did,))
