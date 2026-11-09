"""Audit check E (person review): decision -> freeze -> dry-run -> apply over HTTP.

Asserts the documented batch flow can apply one clean approval to a non-ACTIVE
(SHADOW) run (E-1, green since W4a). The approval picks a candidate the packet
showed and posts the shown packet hash (E-4/D15, E-7). Also asserts the 4xx
answers of E-1/E-3/E-4/E-9/E-14 and the APPLIED status (E-3), and that the queue
page reads document shas in one query per classification (E-13).

Run:  source tools/dev_env.sh && python tools/audit/check_e.py
Uses ONLY the throwaway DB review_e_check_test (created, then dropped). Every DSN the
review routes read or write is forced to it before heimdal is imported.
"""
import hashlib
import json
import os
import sys

DB = "review_e_check_test"  # test-named: migrate refuses others (D-05)
# Same admin/test server as the caller: HEIMDAL_TEST_ADMIN_DSN (set by
# tools/test_env.sh and by the gate's inline env), or the libpq PG* variables.
ADMIN = os.environ.get("HEIMDAL_TEST_ADMIN_DSN") or (
    "postgresql://%s@%s:%s/postgres" % (
        os.environ.get("PGUSER", "postgres"),
        os.environ.get("PGHOST", "localhost"),
        os.environ.get("PGPORT", "5432")))
DSN = ADMIN.rsplit("/", 1)[0] + "/" + DB
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
os.environ.update(HEIMDAL_TEST_DB=DB, HEIMDAL_TEST_ADMIN_DSN=ADMIN,
                  HEIMDAL_PDFHIST_PROJECTION_DSN=DSN, HEIMDAL_PDFHIST_DB=DSN,
                  HEIMDAL_PG_DSN=DSN, HEIMDAL_READ_DB=DSN, HEIMDAL_QA_DB=DSN,
                  HEIMDAL_REVIEW_UI="1")

import psycopg  # noqa: E402

from heimdal.config import DB_URI  # noqa: E402
from heimdal.modules.review import repository as repo  # noqa: E402
from heimdal.pdfhist import consumer  # noqa: E402
from heimdal.pdfhist.projection_migrate import apply_projection_migration  # noqa: E402
from heimdal.pdfhist.testsupport import ensure_test_database  # noqa: E402

assert ensure_test_database() == DSN
assert DB_URI == DSN and consumer.projection_dsn() == DSN  # canonical + projection = throwaway


def seed(c):
    apply_projection_migration(c)
    repo.apply_review_migration(c)
    c.execute("CREATE TABLE canonical_entity (canonical_entity_id bigint PRIMARY KEY,"
              " entity_kind text, display_name text, source_entity_id bigint)")
    c.execute("CREATE TABLE v_role_relation (company_id bigint, person_name text,"
              " person_id bigint, inferred_group text)")
    c.execute("INSERT INTO canonical_entity VALUES (7412,'PERSON','P',1),"
              " (9001,'COMPANY','C',2)")
    # company 1 (regnr 1) is canonical 9001, where 7412 sits on the board, so
    # the packet shows 7412 as a candidate (canonical data newer than the run)
    c.execute("CREATE TABLE company_inventory (registration_number text,"
              " source_entity_id bigint)")
    c.execute("INSERT INTO company_inventory VALUES ('S1', 2)")
    c.execute("INSERT INTO v_role_relation VALUES (9001,'Anna Test',7412,'NEVND')")
    sha = hashlib.sha256(b"e").hexdigest()
    c.execute("INSERT INTO pdf_document_content VALUES (%s,1,'\\x00')", (sha,))
    doc = c.execute("INSERT INTO pdf_document (pdf_type, source_document_id)"
                    " VALUES ('X','d') RETURNING id").fetchone()[0]
    obs = c.execute("INSERT INTO pdf_document_observation (document_id, collection_run_id,"
                    " fetched_at, sha256, byte_length) VALUES (%s,1,now(),%s,1)"
                    " RETURNING id", (doc, sha)).fetchone()[0]
    par = c.execute("INSERT INTO pdf_parse_result (document_observation_id, raw_text,"
                    " structured_data, parse_status) VALUES (%s,'t',%s,'PARSED')"
                    " RETURNING id", (obs, json.dumps(
                        {"changes": [{"entity_normalized": "anna test"}]}))).fetchone()[0]
    ev = c.execute("INSERT INTO pdf_source_event (company_id, requested_regnr, row_skrasnr,"
                   " source_row_key, raw_text, page_no, row_ordinal)"
                   " VALUES (1,'1','1','k','r',0,1) RETURNING id").fetchone()[0]
    roles = {}
    for status in ("ACTIVE", "SHADOW"):
        run = c.execute("INSERT INTO registry_projection.projection_run (projection_version,"
                        " status, code_version, content_hash) VALUES ('v',%s,'t',%s)"
                        " RETURNING projection_run_id", (status, status)).fetchone()[0]
        c.execute("INSERT INTO registry_projection.company_current_state"
                  " (projection_run_id, company_id, registration_number) VALUES (%s,1,'1')",
                  (run,))
        rid = c.execute(
            "INSERT INTO registry_projection.company_person_role (projection_run_id,"
            " company_id, role_type, person_name, entity_normalized, source_scope,"
            " source_document_id, source_observation_id, source_parse_result_id,"
            " source_event_id) VALUES (%s,1,'BOARD','Anna Test','anna test',"
            "'HISTORICAL_EVENT',%s,%s,%s,%s) RETURNING id",
            (run, doc, obs, par, ev)).fetchone()[0]
        c.execute("INSERT INTO registry_projection.entity_link (projection_run_id,"
                  " entity_type, source_entity_key, person_role_id, link_status,"
                  " link_method, link_code_version) VALUES (%s,'PERSON',%s,%s,"
                  "'UNRESOLVED','EXACT_MULTI_ATTRIBUTE','v')", (run, "ROLE:%d" % rid, rid))
        roles[status] = (run, rid)
    return roles


def main():
    c = psycopg.connect(DSN, autocommit=True)
    try:
        roles = seed(c)
        active_role = roles["ACTIVE"][1]
        shadow_run, shadow_role = roles["SHADOW"]
        from heimdal.app import create_app
        app = create_app()
        app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        cl = app.test_client()
        url = "/persons/review/%d" % active_role
        pk = cl.get(url, headers={"Accept": "application/json"}).get_json()
        assert [x["canonical_entity_id"] for x in pk["candidates"]] == [7412], pk
        form = {"decision": "APPROVE_LINK", "shown_packet_hash": pk["packet_hash"]}
        js = {"Accept": "application/json"}
        r = cl.post(url + "/decision", data={**form, "chosen_canonical_id": "9999"},
                    headers=js)
        assert r.status_code == 400, "E-4: off-list approval -> %s" % r.status_code
        r = cl.post(url + "/decision", data={**form, "chosen_canonical_id": "7412",
                                             "shown_packet_hash": "x"}, headers=js)
        assert r.status_code == 409, "E-7: stale packet hash -> %s" % r.status_code
        r = cl.post(url + "/decision", data={**form, "chosen_canonical_id": "7412"},
                    headers=js)
        assert r.status_code == 200, r.status_code
        did = r.get_json()["decision_id"]
        r = cl.post("/persons/review/999999/decision", headers=js, data={
            "decision": "DEFER", "reason_code": "OTHER_DEFER", "shown_packet_hash": "x"})
        assert r.status_code == 404, "E-9: unknown role -> %s" % r.status_code
        for bad, code in (({"target_run_id": roles["ACTIVE"][0]}, 409),
                          ({"target_run_id": 999999}, 404),
                          ({"target_run_id": "abc"}, 400)):
            r = cl.post("/persons/review/batch/freeze", json=bad)
            assert r.status_code == code, "E-1/E-3: freeze %s -> %s" % (bad, r.status_code)
        target = {"target_run_id": shadow_run}
        fr = cl.post("/persons/review/batch/freeze", json=target).get_json()
        assert fr["clean"] == [did], fr
        dr = cl.post("/persons/review/batch/dry-run",
                     json={"frozen_batch_id": fr["frozen_batch_id"], **target}).get_json()
        assert dr["summary"]["clean_applicable"] == 1, dr
        r = cl.post("/persons/review/batch/apply", json={
            "frozen_batch_id": fr["frozen_batch_id"], "target_run_id": shadow_run,
            "state_pin": dr["summary"]["state_pin"]})
        assert r.status_code == 200, "E-1: apply to SHADOW run -> %s %s" % (
            r.status_code, r.get_json())
        assert r.get_json()["applied"] == 1, r.get_json()
        link = c.execute("SELECT link_status, canonical_entity_id, link_method FROM"
                         " registry_projection.entity_link WHERE person_role_id=%s",
                         (shadow_role,)).fetchone()
        assert link == ("LINKED", 7412, "HUMAN-REVIEW-PERSON-v1"), link
        st = c.execute("SELECT application_status, applied_projection_run_id FROM"
                       " registry_projection.person_review_decision WHERE decision_id=%s",
                       (did,)).fetchone()
        assert st == ("APPLIED", shadow_run), "E-3: %s" % (st,)
        r = cl.post("/persons/review/batch/qa/mark",
                    json={"decision_id": 999999, "qa_status": "QA_OK"})
        assert r.status_code == 404, "E-14: qa/mark unknown id -> %s" % r.status_code
        # E-13 (W6d): the queue page reads document shas in one query per
        # classification, however many UNRESOLVED rows there are.
        run = roles["ACTIVE"][0]
        for n in range(5):
            rid = c.execute(
                "INSERT INTO registry_projection.company_person_role (projection_run_id,"
                " company_id, role_type, person_name, entity_normalized, source_scope,"
                " source_document_id, source_observation_id, source_parse_result_id,"
                " source_event_id) SELECT projection_run_id, company_id, role_type,"
                " person_name, %s, source_scope, source_document_id,"
                " source_observation_id, source_parse_result_id, source_event_id"
                " FROM registry_projection.company_person_role WHERE id = %s"
                " RETURNING id", ("e13 %d" % n, active_role)).fetchone()[0]
            c.execute("INSERT INTO registry_projection.entity_link (projection_run_id,"
                      " entity_type, source_entity_key, person_role_id, link_status,"
                      " link_method, link_code_version) VALUES (%s,'PERSON',%s,%s,"
                      "'UNRESOLVED','EXACT_MULTI_ATTRIBUTE','v')", (run, "ROLE:%d" % rid, rid))
        sha_reads = []
        execute = psycopg.Cursor.execute

        def counting(self, query, *a, **k):
            if "FROM pdf_document_observation" in str(query):
                sha_reads.append(query)
            return execute(self, query, *a, **k)
        psycopg.Cursor.execute = counting
        try:
            q = cl.get("/persons/review", headers=js).get_json()
        finally:
            psycopg.Cursor.execute = execute
        assert q["unresolved_total"] >= 6, q
        assert len(sha_reads) <= 2, "E-13: queue page ran %d sha queries" % len(sha_reads)
        print("check_e: OK")
    finally:
        c.close()
        with psycopg.connect(ADMIN, autocommit=True) as a:
            a.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % DB)


if __name__ == "__main__":
    main()
