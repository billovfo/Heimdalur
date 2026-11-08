"""Collector DB rules (audit 2026-11 A-1, A-5, A-14). Isolated, no network.

schema.sql on a scratch DB <HEIMDAL_TEST_DB>_raw, applied twice (idempotent):
A-5:  a timeout is stored as an attempt with error_text and no body (fetch()
      with a scripted urlopen).
A-14: UPDATE, DELETE and TRUNCATE on request_attempt / response_content raise.
On a copy <clone>_rebuild of the heimdal test clone (HEIMDAL_DB, real data):
A-1:  a crashed `process-relations --rebuild` rolls back completely, sequence
      included, and a full rebuild reproduces every relation_id.
Both scratch DBs are dropped at the end.
"""
import io
import pathlib
import types
import urllib.error
import urllib.request

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

import heimdal_collect as hc
from heimdal.pdfhist import db as pdb

SCHEMA = (pathlib.Path(__file__).parent / "schema.sql").read_text()


def _server():
    return pdb.admin_dsn().rsplit("/", 1)[0]


@pytest.fixture(scope="module")
def raw_db():
    name = pdb.require_test_db_name(pdb.test_db_name() + "_raw")
    with psycopg.connect(pdb.admin_dsn(), autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {name}")
    try:
        with psycopg.connect(f"{_server()}/{name}") as conn:
            for _ in range(2):                      # the second apply must be a no-op
                conn.execute(SCHEMA)
                conn.commit()
            yield conn
    finally:
        with psycopg.connect(pdb.admin_dsn(), autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def scripted(monkeypatch, *steps):
    it = iter(steps)

    def fake(req, timeout=None):
        s = next(it)
        if isinstance(s, BaseException):
            raise s
        return s
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(hc, "MIN_SECONDS_BETWEEN_REQUESTS", 0)
    monkeypatch.setattr(hc, "_consecutive_5xx", 0)


class Resp(io.BytesIO):
    status, headers = 200, {}


def test_timeout_is_an_attempt_with_error_text_and_no_body(raw_db, monkeypatch):
    run = hc.start_run(raw_db, "ACQUISITION")
    scripted(monkeypatch, TimeoutError("The read operation timed out"), Resp(b"[]"))
    _aid, sha, payload = hc.fetch(raw_db, run, "get-table", "/api/get-table/7", {"level": 1},
                                  entity_id=7)
    assert payload == []
    rows = raw_db.execute("SELECT retry_number, http_status, error_category, response_sha256,"
                          " error_text FROM request_attempt WHERE collection_run_id = %s"
                          " ORDER BY attempt_id", (run,)).fetchall()
    assert rows == [(0, None, "TIMEOUTERROR", None, "The read operation timed out"),
                    (1, 200, "OK", sha, None)]
    assert raw_db.execute("SELECT count(*) FROM response_content").fetchone()[0] == 1


@pytest.mark.parametrize("sql", [
    "UPDATE request_attempt SET error_text = 'x'", "DELETE FROM request_attempt",
    "TRUNCATE request_attempt CASCADE", "UPDATE response_content SET content_type = 'x'",
    "DELETE FROM response_content", "TRUNCATE response_content CASCADE"])
def test_the_raw_tier_is_append_only(raw_db, sql):
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        raw_db.execute(sql)
    raw_db.rollback()
    assert raw_db.execute("INSERT INTO response_content (response_sha256, response_bytes)"
                          " VALUES (repeat(md5(%s), 2), 'x') ON CONFLICT DO NOTHING"
                          " RETURNING 1", (sql,)).fetchone() == (1,)   # appends still work
    raw_db.commit()


# --- A-1: `process-relations --rebuild` on a copy of the clone ------------------

FINGERPRINT = """
SELECT (SELECT md5(string_agg(t::text, E'\\n' ORDER BY t.relation_id)) FROM (
          SELECT relation_id, raw_from_source_entity_id, raw_from_source_entity_label,
                 raw_to_source_entity_id, raw_to_source_entity_label, raw_source_relation_type_id,
                 raw_source_relation_label, canonical_relation_type, canonical_subject_entity_id,
                 canonical_object_entity_id, semantic_direction_confidence, raw_percentage_label,
                 parsed_percentage, percentage_parse_status, semantic_status, source_attempt_id
          FROM registry_ownership.relation) t),
       (SELECT md5(string_agg(t::text, E'\\n' ORDER BY t.entity_status_id)) FROM (
          SELECT entity_status_id, source_entity_id, canonical_status_type, raw_source_label,
                 source_attempt_id FROM entity_status) t),
       (SELECT md5(string_agg(t::text, E'\\n' ORDER BY t.relation_id)) FROM (
          SELECT relation_id, source_attempt_id, response_sha256, collection_run_id
          FROM registry_ownership.relation_evidence_pointer) t),
       (SELECT count(*) FROM registry_ownership.relation)
"""
SEQUENCE = "SELECT last_value FROM registry_ownership.relation_relation_id_seq"


@pytest.fixture(scope="module")
def rebuild_db():
    clone = pdb.require_test_db_name(conninfo_to_dict(hc.DB_URI or "").get("dbname") or "")
    name = pdb.require_test_db_name(clone + "_rebuild")
    with psycopg.connect(pdb.admin_dsn(), autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (clone,)).fetchone():
            pytest.skip(f"no heimdal test clone {clone} (tools/test_db_refresh.sh)")
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {name} TEMPLATE {clone}")
    try:
        yield f"{_server()}/{name}"
    finally:
        with psycopg.connect(pdb.admin_dsn(), autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def _state(dsn):
    with psycopg.connect(dsn) as c:
        return c.execute(FINGERPRINT).fetchone(), c.execute(SEQUENCE).fetchone()[0]


def test_rebuild_reproduces_relation_ids_and_a_crash_rolls_back(rebuild_db, monkeypatch):
    before, seq_before = _state(rebuild_db)
    assert before[3] > 0
    monkeypatch.setattr(hc, "DB_URI", rebuild_db)
    real, seen = hc.classify_edge, [0]

    def crash_halfway(edge):
        seen[0] += 1
        if seen[0] == before[3] // 2:
            raise RuntimeError("simulated crash")
        return real(edge)
    monkeypatch.setattr(hc, "classify_edge", crash_halfway)
    with pytest.raises(RuntimeError, match="simulated crash"):
        hc.cmd_process_relations(types.SimpleNamespace(rebuild=True))
    assert _state(rebuild_db) == (before, seq_before)       # TRUNCATE and the restart rolled back
    with psycopg.connect(rebuild_db) as c:
        assert c.execute("SELECT run_status, stop_reason FROM collection_run"
                         " ORDER BY collection_run_id DESC LIMIT 1").fetchone() == (
            "STOPPED", "crashed: RuntimeError: simulated crash")

    monkeypatch.setattr(hc, "classify_edge", real)
    hc.cmd_process_relations(types.SimpleNamespace(rebuild=True))
    assert _state(rebuild_db)[0] == before                  # same ids, same rows
