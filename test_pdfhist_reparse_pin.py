"""Wave X2 tests: offline re-parse, parser pin, link build, activate guard.

Isolated DB (heimdal.pdfhist.testsupport), canonical clone read-only via
HEIMDAL_QA_DB. Covers four areas of re-parsing, link building and activation:

1. parser_version pin through build_shadow -> _build_content -> all four
   selectors, single-run and cross-run, with the unpinned path unchanged;
2. reparse --parser-version: insert, resume/idempotency, empty text,
   parser error, non-default version refused;
3. build-links on a SHADOW run and its refusal on an ACTIVE run;
4. activate refusing a run with no entity_link rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import psycopg
import pytest
from psycopg.types.json import Json

from heimdal.pdfhist import ingest, projection
from heimdal.pdfhist.testsupport import ensure_test_database
from scripts import registry_projection_admin as admin

RUN = 515151
COMPANY = 9501
PUBLIC_SNAPSHOT = os.environ.get("HEIMDAL_GATE_INLINE") == "1"


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _content(conn, token: str) -> str:
    sha = _sha(token)
    conn.execute(
        "INSERT INTO pdf_document_content (sha256, byte_length, content) "
        "VALUES (%s,%s,%s) ON CONFLICT (sha256) DO NOTHING",
        (sha, len(token), token.encode()))
    return sha


def _document(conn, pdf_type: str, source_document_id: str) -> int:
    conn.execute(
        "INSERT INTO pdf_document (pdf_type, source_document_id) "
        "VALUES (%s,%s) ON CONFLICT DO NOTHING", (pdf_type, source_document_id))
    return conn.execute(
        "SELECT id FROM pdf_document WHERE pdf_type=%s AND "
        "source_document_id=%s", (pdf_type, source_document_id)).fetchone()[0]


def _observation(conn, document_id: int, content_token: str,
                 run_id: int = RUN, snapshot_date=None) -> int:
    return conn.execute(
        "INSERT INTO pdf_document_observation (document_id, collection_run_id, "
        "fetched_at, document_snapshot_date, sha256, byte_length, extracted_text) "
        "VALUES (%s,%s,now(),%s,%s,%s,'stored') RETURNING id",
        (document_id, run_id, snapshot_date, _content(conn, content_token),
         len(content_token))).fetchone()[0]


def _parse(conn, observation_id: int, parser_version: str, document_kind: str,
           raw_text: str, structured_data: dict) -> int:
    return conn.execute(
        "INSERT INTO pdf_parse_result (document_observation_id, parser_version,"
        " document_kind, raw_text, structured_data, parse_status)"
        " VALUES (%s,%s,%s,%s,%s,'PARSED') RETURNING id",
        (observation_id, parser_version, document_kind, raw_text,
         Json(structured_data))).fetchone()[0]


def _seed_two_versions(conn) -> None:
    """One Felag and one Lysing observation, each with a v1 and v2 parse.

    The two versions carry different content: a v1-only person/event and a
    v2-only person/event (the v2 event is the E2 FOUNDING entry).
    """
    if conn.execute(
            "SELECT count(*) FROM pdf_document WHERE "
            "source_document_id='X2-F-9501'").fetchone()[0]:
        return
    felag_doc = _document(conn, "FelagPdf", "X2-F-9501")
    lys_doc = _document(conn, "LysingPdf", "X2-L-9501")
    felag_obs = _observation(conn, felag_doc, "x2-felag", snapshot_date="2026-08-31")
    lys_obs = _observation(conn, lys_doc, "x2-lysing")

    v1_snapshot = {
        "registration_number": "9501", "company_name": "P/F V1",
        "board": [{"name": "V1 Only", "role": "Stjóri"}], "management": [],
        "snapshot_date": "2026-08-31"}
    v2_snapshot = {
        "registration_number": "9501", "company_name": "P/F V2",
        "board": [{"name": "V2 Only", "role": "Stjóri"}], "management": [],
        "snapshot_date": "2026-08-31"}
    _parse(conn, felag_obs, "1", "COMPANY_SNAPSHOT", "snap-v1", v1_snapshot)
    _parse(conn, felag_obs, "2", "COMPANY_SNAPSHOT", "snap-v2", v2_snapshot)

    v1_changes = [{
        "type": "NAME_CHANGE", "action": "CHANGED", "entity": "V1 Only Event",
        "entity_normalized": "v1 only event", "effective_date": "2021-05-01"}]
    v2_changes = [{
        "type": "BOARD_CHANGE", "action": "APPOINTED",
        "entity": "V2 Founding Person", "entity_normalized": "v2 founding person",
        "raw_text": "V2 stiftelse", "effective_date": "1977-01-01",
        "origin": "FOUNDING"}]
    _parse(conn, lys_obs, "1", "REGISTRATION_NOTICE", "lys-v1",
           {"registration_number": "9501", "changes": v1_changes})
    _parse(conn, lys_obs, "2", "REGISTRATION_NOTICE", "lys-v2",
           {"registration_number": "9501", "changes": v2_changes})

    event_id = conn.execute(
        "INSERT INTO pdf_source_event (company_id, requested_regnr, row_skrasnr,"
        " source_row_key, source_event_date, raw_text, page_no, row_ordinal)"
        " VALUES (%s,'9501','9501','x2-k-9501','2021-05-01','row',0,1)"
        " RETURNING id", (COMPANY,)).fetchone()[0]
    for document_id, role in ((felag_doc, "FELAG_CELL"),
                              (lys_doc, "LYSING_CELL")):
        conn.execute(
            "INSERT INTO pdf_source_event_document (source_event_id, document_id,"
            " cell_role) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (event_id, document_id, role))


def _seed_reparse_observation(conn, source_document_id: str, pdf_type: str,
                              raw_text: str, parser_version: str = "1") -> int:
    """One observation with a single parse of the given parser version."""
    document_id = _document(conn, pdf_type, source_document_id)
    observation_id = _observation(conn, document_id, f"content-{source_document_id}")
    _parse(conn, observation_id, parser_version,
           "COMPANY_SNAPSHOT" if pdf_type == "FelagPdf"
           else "REGISTRATION_NOTICE", raw_text,
           {"registration_number": "9501", "changes": []})
    return observation_id


def _person_names(conn, run_id: int) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT person_name FROM registry_projection.company_person_role "
        "WHERE projection_run_id=%s", (run_id,)).fetchall()}


def _event_entities(conn, run_id: int) -> set[str]:
    values = [r[0] for r in conn.execute(
        "SELECT value_json FROM registry_projection.company_history_event "
        "WHERE projection_run_id=%s", (run_id,)).fetchall()]
    return {(v or {}).get("entity") for v in values}


def _source_parser_version(conn, run_id: int):
    return conn.execute(
        "SELECT source_parser_version FROM registry_projection.projection_run "
        "WHERE projection_run_id=%s", (run_id,)).fetchone()[0]


class _RawRecorder:
    """Records SQL verbatim, so whitespace-only lines stay visible."""

    def __init__(self):
        self.sqls = []

    def execute(self, sql, params=()):
        self.sqls.append(sql)
        return self

    def fetchall(self):
        return []


# The fdccce0 (pre-pin) selectors, kept verbatim as the byte-identity oracle.
def _old_latest_snapshots(conn, run_id):
    return conn.execute("""
        SELECT DISTINCT ON (s.company_id)
               s.company_id, s.valid_from, p.id, p.structured_data,
               p.parser_version, o.document_id, o.id, s.sha256
        FROM company_pdf_snapshot_history s
        JOIN pdf_document_observation o ON o.id = s.document_observation_id
             AND o.collection_run_id = %s
        JOIN pdf_parse_result p ON p.document_observation_id = o.id
             AND p.document_kind = 'COMPANY_SNAPSHOT'
             AND p.parse_status = 'PARSED'
        JOIN pdf_document d ON d.id = o.document_id AND d.pdf_type = 'FelagPdf'
        ORDER BY s.company_id, s.valid_from DESC, o.id DESC  -- D-10: newest
        """, (run_id,)).fetchall()


def _old_history_events(conn, run_id):
    sql = """
        SELECT ev.id, ev.company_id, ev.row_skrasnr, ev.source_event_date,
               ev.raw_text, d.id AS document_id, o.id AS observation_id,
               p.id AS parse_id, p.structured_data, o.collection_run_id
        FROM pdf_source_event ev
        JOIN pdf_source_event_document ld ON ld.source_event_id = ev.id
        JOIN pdf_document d ON d.id = ld.document_id
             AND d.pdf_type = 'LysingPdf'
        JOIN pdf_document_observation o ON o.document_id = d.id
        JOIN pdf_parse_result p ON p.document_observation_id = o.id
             AND p.document_kind = 'REGISTRATION_NOTICE'
             AND p.parse_status = 'PARSED'
        {run_filter}
        ORDER BY ev.id, p.id
        """
    return conn.execute(
        sql.format(run_filter="WHERE o.collection_run_id = %s"
                   if run_id is not None else ""),
        (run_id,) if run_id is not None else None).fetchall()


def _old_cross_run_candidates(conn):
    return conn.execute("""
        SELECT s.company_id, s.valid_from, p.id, p.structured_data,
               p.parser_version, o.document_id, o.id, s.sha256,
               o.collection_run_id
        FROM company_pdf_snapshot_history s
        JOIN pdf_document_observation o ON o.id = s.document_observation_id
        JOIN pdf_parse_result p ON p.document_observation_id = o.id
             AND p.document_kind = 'COMPANY_SNAPSHOT'
             AND p.parse_status = 'PARSED'
        JOIN pdf_document d ON d.id = o.document_id
             AND d.pdf_type = 'FelagPdf'
        ORDER BY s.company_id, s.valid_from, o.id
        """).fetchall()


@pytest.fixture(scope="module")
def db():
    return ensure_test_database()


@pytest.fixture()
def conn(db):
    with psycopg.connect(db) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. parser pin


def test_single_run_pin_contains_only_pinned_version(conn, capsys):
    _seed_two_versions(conn)
    conn.commit()
    admin.cmd_build_shadow(argparse.Namespace(
        source_run=RUN, cross_run=False, version="x2-pin-single", notes="",
        parser_version="2", dsn=conn.info.dsn))
    run_id = json.loads(capsys.readouterr().out)["projection_run_id"]
    names = _person_names(conn, run_id)
    assert "V2 Only" in names
    assert "V1 Only" not in names
    entities = _event_entities(conn, run_id)
    assert "V2 Founding Person" in entities
    assert "V1 Only Event" not in entities
    assert _source_parser_version(conn, run_id) == "2"


def test_cross_run_pin_contains_only_pinned_version(conn):
    _seed_two_versions(conn)
    conn.commit()
    run_id = projection.build_shadow(
        conn, cross_run=True, projection_version="x2-pin-cross",
        parser_version="2")
    conn.commit()
    names = _person_names(conn, run_id)
    assert "V2 Only" in names
    assert "V1 Only" not in names
    entities = _event_entities(conn, run_id)
    assert "V2 Founding Person" in entities
    assert "V1 Only Event" not in entities
    assert _source_parser_version(conn, run_id) == "2"


def test_no_pin_keeps_old_mixed_behaviour(conn):
    _seed_two_versions(conn)
    conn.commit()
    single = projection.build_shadow(
        conn, source_collection_run_id=RUN, projection_version="x2-nopin-single")
    cross = projection.build_shadow(
        conn, cross_run=True, projection_version="x2-nopin-cross")
    conn.commit()
    for run_id in (single, cross):
        entities = _event_entities(conn, run_id)
        assert "V1 Only Event" in entities
        assert "V2 Founding Person" in entities


def test_pinned_snapshot_source_never_mixes_versions(conn):
    _seed_two_versions(conn)
    conn.commit()
    single = projection._latest_snapshots(conn, RUN, parser_version="2")
    assert len(single) == 1
    assert single[0][4] == "2"
    assert single[0][3]["company_name"] == "P/F V2"

    cross, stats = projection.latest_snapshots_cross_run(
        conn, parser_version="2")
    assert stats["candidate_snapshot_rows"] == 1
    assert stats["duplicate_groups"] == 0
    assert stats["regeneration_groups"] == 0
    assert stats["companies_selected"] == 1
    assert cross[0][4] == "2"
    assert cross[0][3]["company_name"] == "P/F V2"


def test_unpinned_sql_is_byte_identical_to_fdccce0():
    cases = (
        (lambda rec: projection._latest_snapshots(rec, RUN),
         lambda rec: _old_latest_snapshots(rec, RUN)),
        (lambda rec: projection._history_events(rec, RUN),
         lambda rec: _old_history_events(rec, RUN)),
        (lambda rec: projection._history_events(rec, None),
         lambda rec: _old_history_events(rec, None)),
        (lambda rec: projection._history_events_cross_run(rec),
         lambda rec: _old_history_events(rec, None)),
    )
    for new_call, old_call in cases:
        new_rec, old_rec = _RawRecorder(), _RawRecorder()
        new_call(new_rec)
        old_call(old_rec)
        assert new_rec.sqls == old_rec.sqls

    new_rec, old_rec = _RawRecorder(), _RawRecorder()
    projection.latest_snapshots_cross_run(new_rec)
    _old_cross_run_candidates(old_rec)
    assert new_rec.sqls[0] == old_rec.sqls[0]


def test_pinned_build_stores_explicit_pin_without_snapshot(conn):
    run_id = projection.build_shadow(
        conn, source_collection_run_id=RUN + 7000,
        projection_version="x2-pin-empty", parser_version="2")
    conn.commit()
    assert projection.one_count(conn, run_id, "company_current_state") == 0
    assert _source_parser_version(conn, run_id) == "2"


# ---------------------------------------------------------------------------
# 2. reparse


def test_reparse_inserts_v2_and_second_run_is_idempotent(conn, capsys):
    observation_id = _seed_reparse_observation(
        conn, "X2-R-1", "FelagPdf", "Skrásetingar-nr. 9501\nDagf. 31-08-2026\n")
    conn.commit()
    first = admin._reparse(conn, ingest.PARSER_VERSION_DEFAULT, batch_size=1)
    assert first["seen"] >= 1
    assert first["inserted"] >= 1
    assert first["duration_seconds"] >= 0
    assert conn.execute(
        "SELECT count(*) FROM pdf_parse_result WHERE "
        "document_observation_id=%s AND parser_version=%s",
        (observation_id, ingest.PARSER_VERSION_DEFAULT)).fetchone()[0] == 1

    second = admin._reparse(conn, ingest.PARSER_VERSION_DEFAULT, batch_size=1)
    assert second["seen"] == 0
    assert second["inserted"] == 0
    assert second["already_present"] == 0

    admin.cmd_reparse(argparse.Namespace(
        parser_version=ingest.PARSER_VERSION_DEFAULT, dsn=conn.info.dsn))
    cli_second = json.loads(capsys.readouterr().out)
    assert cli_second["seen"] == 0
    assert cli_second["inserted"] == 0


def test_reparse_skips_empty_raw_text(conn):
    observation_id = _seed_reparse_observation(
        conn, "X2-R-EMPTY", "LysingPdf", "")
    conn.commit()
    summary = admin._reparse(conn, ingest.PARSER_VERSION_DEFAULT)
    assert summary["skipped_empty"] >= 1
    assert conn.execute(
        "SELECT count(*) FROM pdf_parse_result WHERE "
        "document_observation_id=%s AND parser_version=%s",
        (observation_id, ingest.PARSER_VERSION_DEFAULT)).fetchone()[0] == 0
    # leave a completed DB for the resume assertions
    conn.execute("DELETE FROM pdf_parse_result WHERE document_observation_id=%s",
                 (observation_id,))
    conn.execute("DELETE FROM pdf_document_observation WHERE id=%s",
                 (observation_id,))
    conn.commit()


def test_reparse_skips_parser_error_and_records_id(conn, monkeypatch):
    observation_id = _seed_reparse_observation(
        conn, "X2-R-ERROR", "FelagPdf", "this parser will raise")
    conn.commit()

    def _boom(text):
        raise ValueError("parser exploded")

    monkeypatch.setattr(ingest, "parse_felag_pdf", _boom)
    summary = admin._reparse(conn, ingest.PARSER_VERSION_DEFAULT)
    assert {"observation_id": observation_id, "error_type": "ValueError"} in [
        {k: item[k] for k in ("observation_id", "error_type")}
        for item in summary["skipped_error"]]
    assert conn.execute(
        "SELECT count(*) FROM pdf_parse_result WHERE "
        "document_observation_id=%s AND parser_version=%s",
        (observation_id, ingest.PARSER_VERSION_DEFAULT)).fetchone()[0] == 0
    # leave a completed DB for the resume assertions
    conn.execute("DELETE FROM pdf_parse_result WHERE document_observation_id=%s",
                 (observation_id,))
    conn.execute("DELETE FROM pdf_document_observation WHERE id=%s",
                 (observation_id,))
    conn.commit()


def test_reparse_stores_shared_parse_status_and_review_fields(conn, monkeypatch):
    observation_id = _seed_reparse_observation(
        conn, "X2-R-REVIEW", "FelagPdf", "review input")
    conn.commit()

    def _review(text):
        assert text == "review input"
        return {"document_kind": "COMPANY_SNAPSHOT", "needs_review": True,
                "review_reasons": ["FIRST_REASON", "SECOND_REASON"]}

    monkeypatch.setattr(ingest, "parse_felag_pdf", _review)
    summary = admin._reparse(conn, ingest.PARSER_VERSION_DEFAULT)
    assert summary["inserted"] >= 1
    row = conn.execute(
        "SELECT document_kind, parse_status, needs_review, review_reason "
        "FROM pdf_parse_result WHERE document_observation_id=%s AND "
        "parser_version=%s",
        (observation_id, ingest.PARSER_VERSION_DEFAULT)).fetchone()
    assert row == ("COMPANY_SNAPSHOT", "REVIEW", True,
                   "FIRST_REASON,SECOND_REASON")


def test_reparse_completed_db_sees_nothing(conn):
    admin._reparse(conn, ingest.PARSER_VERSION_DEFAULT)  # drain leftovers
    summary = admin._reparse(conn, ingest.PARSER_VERSION_DEFAULT)
    assert summary["seen"] == 0
    assert summary["inserted"] == 0
    assert summary["already_present"] == 0
    assert summary["skipped_empty"] == 0
    assert summary["skipped_error"] == []


def test_reparse_ignores_observation_with_only_target_version(conn):
    admin._reparse(conn, ingest.PARSER_VERSION_DEFAULT)  # drain leftovers
    observation_id = _seed_reparse_observation(
        conn, "X2-R-V2ONLY", "FelagPdf", "only the target version",
        parser_version=ingest.PARSER_VERSION_DEFAULT)
    conn.commit()
    summary = admin._reparse(conn, ingest.PARSER_VERSION_DEFAULT)
    assert summary["seen"] == 0
    assert summary["inserted"] == 0
    assert conn.execute(
        "SELECT count(*) FROM pdf_parse_result WHERE "
        "document_observation_id=%s AND parser_version=%s",
        (observation_id, ingest.PARSER_VERSION_DEFAULT)).fetchone()[0] == 1


def test_reparse_never_changes_existing_rows(conn):
    _seed_two_versions(conn)
    conn.commit()

    def _fixture_rows():
        return [r[0] for r in conn.execute(
            "SELECT to_jsonb(p)::text FROM pdf_parse_result p "
            "JOIN pdf_document_observation o "
            "ON o.id = p.document_observation_id "
            "JOIN pdf_document d ON d.id = o.document_id "
            "WHERE d.source_document_id IN ('X2-F-9501', 'X2-L-9501') "
            "ORDER BY p.id").fetchall()]

    before = _fixture_rows()
    _seed_reparse_observation(
        conn, "X2-R-UNCHANGED", "FelagPdf",
        "Skrásetingar-nr. 9501\nDagf. 31-08-2026\n")
    conn.commit()
    summary = admin._reparse(conn, ingest.PARSER_VERSION_DEFAULT)
    assert summary["inserted"] >= 1
    assert _fixture_rows() == before


def test_reparse_refuses_non_default_parser_version(conn):
    args = argparse.Namespace(parser_version="1", dsn=None)
    with pytest.raises(SystemExit) as excinfo:
        admin.cmd_reparse(args)
    assert "refused" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# 3 + 4. build-links and the activate guard


def test_activate_refuses_run_without_entity_links(conn):
    _seed_two_versions(conn)
    conn.commit()
    run_id = projection.build_shadow(
        conn, source_collection_run_id=RUN, projection_version="x2-links",
        parser_version="2")
    conn.commit()

    with pytest.raises(SystemExit) as excinfo:
        admin.cmd_activate(argparse.Namespace(run_id=run_id, dsn=None))
    assert "entity_link" in str(excinfo.value)
    assert "build-links" in str(excinfo.value)


@pytest.mark.skipif(PUBLIC_SNAPSHOT, reason="build-links needs the canonical clone")
def test_build_links_shadow_then_activate_and_refuse_active(conn, capsys):
    _seed_two_versions(conn)
    conn.commit()
    run_id = projection.build_shadow(
        conn, source_collection_run_id=RUN, projection_version="x2-links",
        parser_version="2")
    conn.commit()

    admin.cmd_build_links(argparse.Namespace(run_id=run_id))
    printed = json.loads(capsys.readouterr().out)
    assert printed["run_id"] == run_id
    assert printed["link_count_by_tier"]["COMPANY"] >= 1
    assert sum(printed["link_count_by_tier"].values()) >= 1
    assert conn.execute(
        "SELECT count(*) FROM registry_projection.entity_link "
        "WHERE projection_run_id=%s", (run_id,)).fetchone()[0] >= 1

    admin.cmd_activate(argparse.Namespace(run_id=run_id, dsn=None))
    assert projection.active_run_id(conn) == run_id

    with pytest.raises(SystemExit) as excinfo:
        admin.cmd_build_links(argparse.Namespace(run_id=run_id))
    assert "refused" in str(excinfo.value).lower()


def test_activate_unknown_run_reports_unknown(conn):
    missing = conn.execute(
        "SELECT coalesce(max(projection_run_id), 0) + 1 FROM "
        "registry_projection.projection_run").fetchone()[0]
    with pytest.raises(SystemExit) as excinfo:
        admin.cmd_activate(argparse.Namespace(run_id=missing,
                                             dsn=conn.info.dsn))
    assert "unknown run" in str(excinfo.value).lower()


class _FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_build_links_closes_projection_when_canonical_open_fails(monkeypatch):
    projection_conn = _FakeConn()
    calls = []

    def fake_connect(dsn, **kwargs):
        calls.append(dsn)
        if len(calls) == 1:
            return projection_conn
        raise OSError("canonical unavailable")

    monkeypatch.setattr(admin.psycopg, "connect", fake_connect)
    monkeypatch.setenv("HEIMDAL_PDFHIST_PROJECTION_DSN",
                       "postgresql://postgres@localhost/test_projection")
    monkeypatch.setenv("HEIMDAL_QA_DB",
                       "postgresql://postgres@localhost/test_canonical")
    with pytest.raises(OSError):
        admin.cmd_build_links(argparse.Namespace(run_id=1))
    assert projection_conn.closed


def test_build_links_requires_explicit_qa_db(monkeypatch):
    """X5b review #3: no HEIMDAL_READ_DB/default fallback, no connection."""
    calls = []
    monkeypatch.delenv("HEIMDAL_QA_DB", raising=False)
    monkeypatch.delenv("HEIMDAL_READ_DB", raising=False)
    monkeypatch.setenv("HEIMDAL_PDFHIST_PROJECTION_DSN",
                       "postgresql://postgres@localhost/test_projection")
    monkeypatch.setattr(admin.psycopg, "connect",
                        lambda *a, **k: calls.append((a, k)))
    with pytest.raises(SystemExit) as excinfo:
        admin.cmd_build_links(argparse.Namespace(run_id=1))
    assert "HEIMDAL_QA_DB" in str(excinfo.value)
    assert calls == []


class _DbNameConn:
    def __init__(self, dbname):
        self.dbname = dbname
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=()):
        assert "current_database()" in sql
        return self

    def fetchone(self):
        return (self.dbname,)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_build_links_canonical_is_read_only_and_both_databases_reported(
        monkeypatch, capsys):
    """X5b review #3: canonical connects read-only; stats name both DBs."""
    calls = []

    def fake_connect(dsn, **kwargs):
        calls.append((dsn, kwargs))
        return _DbNameConn("proj_db" if len(calls) == 1 else "canon_db")

    monkeypatch.setattr(admin.psycopg, "connect", fake_connect)
    monkeypatch.setenv("HEIMDAL_PDFHIST_PROJECTION_DSN",
                       "postgresql://postgres@localhost/test_projection")
    monkeypatch.setenv("HEIMDAL_QA_DB",
                       "postgresql://postgres@localhost/test_canonical")
    monkeypatch.setattr(
        admin.entity_links, "build_entity_links",
        lambda run_id, **kwargs: {
            "COMPANY": {"LINKED": 1, "UNRESOLVED": 0, "AMBIGUOUS": 0},
            "PERSON": {"LINKED": 0, "UNRESOLVED": 0, "AMBIGUOUS": 0}})

    admin.cmd_build_links(argparse.Namespace(run_id=7))

    printed = json.loads(capsys.readouterr().out)
    assert printed["databases"] == {"projection": "proj_db",
                                    "canonical": "canon_db"}
    assert [dsn for dsn, _ in calls] == [
        "postgresql://postgres@localhost/test_projection",
        "postgresql://postgres@localhost/test_canonical"]
    assert calls[0][1] == {}
    assert calls[1][1] == {
        "options": "-c default_transaction_read_only=on"}


@pytest.mark.skipif(PUBLIC_SNAPSHOT, reason="build-links needs the canonical clone")
@pytest.mark.parametrize("status", ["RETIRED", "FAILED"])
def test_build_links_refuses_every_status_but_shadow(conn, capsys, status):
    """X5b review #4: RETIRED (rollback target) and FAILED are immutable."""
    run_id = projection.build_shadow(
        conn, source_collection_run_id=RUN, projection_version="x5b-status",
        parser_version="2")
    conn.execute(
        "UPDATE registry_projection.projection_run SET status=%s "
        "WHERE projection_run_id=%s", (status, run_id))
    # review fix round 1 #3: a sentinel link proves the refusal happens
    # before any DELETE (a zero-row run cannot show that).
    conn.execute(
        "INSERT INTO registry_projection.entity_link "
        "(projection_run_id, entity_type, source_entity_key, "
        " registration_number, link_status, link_method, link_code_version) "
        "VALUES (%s,'COMPANY','SENTINEL','SENTINEL','UNRESOLVED',"
        "        'REGISTRATION_NUMBER_EXACT','test')", (run_id,))
    conn.commit()

    with pytest.raises(SystemExit) as excinfo:
        admin.cmd_build_links(argparse.Namespace(run_id=run_id))
    assert "refused" in str(excinfo.value).lower()
    assert status in str(excinfo.value)
    assert "COV-HISTORICAL" not in str(excinfo.value)
    assert conn.execute(
        "SELECT source_entity_key FROM registry_projection.entity_link "
        "WHERE projection_run_id=%s", (run_id,)).fetchall() == [("SENTINEL",)]


def test_build_links_help_documents_env_connections(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["registry_projection_admin", "build-links", "--help"])
    with pytest.raises(SystemExit) as excinfo:
        admin.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "HEIMDAL_PDFHIST_PROJECTION_DSN" in out
    assert "HEIMDAL_QA_DB" in out
    assert "--dsn" in out
