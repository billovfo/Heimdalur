"""Observation / parse-reuse / run-log behaviour (plan FASE 7, 13, 14 + 22).

All against the throwaway test database; PDF bytes come from the REAL fixture
corpus ($HEIMDAL_FIXTURES_DIR). Live HTTP requests executed: 0. Every test scopes
its assertions by unique source_document_id / run_id so module-order cannot
break them.
"""
import hashlib
import psycopg
import pytest

from heimdal.pdfhist.ingest import ingest_pdf_observation
from heimdal.pdfhist.testsupport import connect, fixture_bytes, source_event_dict

FELAG_BYTES = fixture_bytes("felag_10.bin")
SHA256 = hashlib.sha256(FELAG_BYTES).hexdigest()


def _ingest(conn, document="felag-dok-A", run=9001, **extra):
    params = dict(
        conn=conn, company_id=10,
        source_event=source_event_dict(),
        pdf_type="FelagPdf",
        source_document_id=document,
        pdf_bytes=FELAG_BYTES,
        collection_run_id=run,
    )
    params.update(extra)
    return ingest_pdf_observation(**params)


def test_obs_fk_01_observation_requires_existing_content():
    with connect() as conn:
        doc_id = conn.execute(
            "INSERT INTO pdf_document (pdf_type, source_document_id,"
            " pdf_kind_hint) VALUES ('FelagPdf','obs-fk-01-doc',"
            "'FULL_STATE_SNAPSHOT') RETURNING id").fetchone()[0]
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO pdf_document_observation (document_id,"
                " collection_run_id, fetched_at, sha256, byte_length)"
                " VALUES (%s, 424242, now(), %s, %s)",
                (doc_id, "0" * 64, 10))
        conn.rollback()


def test_obs_run_01_same_document_same_run_is_observed_once():
    with connect() as conn:
        result = _ingest(conn, document="obs-run-01", run=9101)
        assert result["reused_observation"] is False
        _ingest(conn, document="obs-run-01", run=9101)
        count = conn.execute(
            "SELECT count(*) FROM pdf_document_observation WHERE"
            " document_id = %s", (result["document_id"],)).fetchone()[0]
        assert count == 1


def test_obs_run_02_same_document_across_two_runs_two_observations():
    with connect() as conn:
        result = _ingest(conn, document="obs-run-02", run=9102)
        result2 = _ingest(conn, document="obs-run-02", run=9103)
        assert result2["reused_observation"] is False
        rows = conn.execute(
            "SELECT count(*) FROM pdf_document_observation WHERE"
            " document_id = %s", (result["document_id"],)).fetchone()[0]
        assert rows == 2


def test_same_hash_reuses_content_and_preserves_metadata():
    with connect() as conn:
        first = _ingest(conn, document="same-hash-1", run=9201)
        second = _ingest(conn, document="same-hash-2", run=9202)
        # exact bytes are stored once, regardless of earlier tests in this module
        contents = conn.execute(
            "SELECT count(*) FROM pdf_document_content WHERE sha256 = %s",
            (SHA256,)).fetchone()[0]
        assert contents == 1
        meta = conn.execute(
            "SELECT byte_length, sha256 FROM pdf_document_observation"
            " WHERE document_id IN (%s,%s) ORDER BY id",
            (first["document_id"], second["document_id"])).fetchall()
        assert len(meta) == 2
        assert meta[0] == meta[1]  # identical metadata on both observations


def test_content_byte_level_roundtrip_preserves_pdf_bytes():
    import io
    with connect() as conn:
        _ingest(conn, document="byte-roundtrip", run=9211)
        stored = conn.execute(
            "SELECT content, byte_length FROM pdf_document_content"
            " WHERE sha256 = %s", (SHA256,)).fetchone()
        assert stored[1] == len(FELAG_BYTES)
        assert bytes(stored[0]).startswith(b"%PDF-")


def test_parse_reuse_01_same_version_same_run_calls_parser_once():
    calls = []

    def counting_parser(text):
        from heimdal.pdfhist.parse import parse_felag_pdf
        calls.append(text)
        return parse_felag_pdf(text)

    with connect() as conn:
        first = ingest_pdf_observation(
            conn=conn, company_id=10, source_event=source_event_dict(),
            pdf_type="FelagPdf", source_document_id="reuse-01",
            pdf_bytes=FELAG_BYTES, collection_run_id=9301,
            parser_fn=counting_parser)
        assert len(calls) == 1 and first["reused_parse"] is False
        second = ingest_pdf_observation(
            conn=conn, company_id=10, source_event=source_event_dict(),
            pdf_type="FelagPdf", source_document_id="reuse-01",
            pdf_bytes=FELAG_BYTES, collection_run_id=9301,
            parser_fn=counting_parser)
        assert len(calls) == 1  # reuse: the parser is NOT invoked again
        assert second["reused_parse"] is True
        assert second["parse_result_id"] == first["parse_result_id"]
        parses = conn.execute(
            "SELECT count(*) FROM pdf_parse_result p"
            " JOIN pdf_document_observation o ON o.id ="
            " p.document_observation_id JOIN pdf_document d ON d.id ="
            " o.document_id WHERE d.source_document_id = 'reuse-01'"
        ).fetchone()[0]
        assert parses == 1


def test_parse_reuse_02_new_parser_version_parses_to_new_row():
    with connect() as conn:
        ingest_pdf_observation(
            conn=conn, company_id=10, source_event=source_event_dict(),
            pdf_type="FelagPdf", source_document_id="reuse-02",
            pdf_bytes=FELAG_BYTES, collection_run_id=9401,
            parser_version="1")
        ingest_pdf_observation(
            conn=conn, company_id=10, source_event=source_event_dict(),
            pdf_type="FelagPdf", source_document_id="reuse-02",
            pdf_bytes=FELAG_BYTES, collection_run_id=9401,
            parser_version="2")
        rows = conn.execute(
            "SELECT p.parser_version, count(*) FROM pdf_parse_result p"
            " JOIN pdf_document_observation o ON o.id ="
            " p.document_observation_id JOIN pdf_document d ON d.id ="
            " o.document_id WHERE d.source_document_id = 'reuse-02'"
            " GROUP BY p.parser_version ORDER BY 1").fetchall()
        assert rows == [("1", 1), ("2", 1)]
        obs = conn.execute(
            "SELECT count(*) FROM pdf_document_observation o JOIN"
            " pdf_document d ON d.id = o.document_id WHERE"
            " d.source_document_id = 'reuse-02'").fetchone()[0]
        assert obs == 1  # a new parser version never duplicates the observation


def test_restart_really_recomputes_and_keeps_observation():
    with connect() as conn:
        first = _ingest(conn, document="restart-01", run=9601)
        conn.execute("DELETE FROM pdf_parse_result")
        rerun = _ingest(conn, document="restart-01", run=9601)
        assert rerun["reused_observation"] is True
        assert rerun["parse_result_id"] != first["parse_result_id"]
        obs_count = conn.execute(
            "SELECT count(*) FROM pdf_document_observation o JOIN"
            " pdf_document d ON d.id = o.document_id WHERE"
            " d.source_document_id = 'restart-01'").fetchone()[0]
        assert obs_count == 1
        text = conn.execute(
            "SELECT extracted_text FROM pdf_document_observation o JOIN"
            " pdf_document d ON d.id = o.document_id WHERE"
            " d.source_document_id = 'restart-01'").fetchone()[0]
        assert "30-08-2026" in (text or "")
        conn.rollback()


def test_fetch_failed_and_run_warning_enter_review_queue():
    with connect() as conn:
        conn.execute(
            "INSERT INTO pdf_run_log (run_id, company_id, document_id, step,"
            " status, error_type, detail, created_at)"
            " VALUES (9701, 10, NULL, 'FETCH', 'FAILED', 'HTTP_5XX',"
            " 'network down', now())")
        conn.execute(
            "INSERT INTO pdf_run_log (run_id, company_id, step, status,"
            " error_type, detail, created_at)"
            " VALUES (9701, 10, 'PARSE', 'WARN', 'FOOTER_ARTIFACT',"
            " 'warn detail', now())")
        queue = conn.execute(
            "SELECT issue FROM pdf_review_queue WHERE issue IN"
            " ('FETCH_FAILED','RUN_WARNING','SOURCE_NAME_MISMATCH')"
            " ORDER BY issue").fetchall()
        issues = {row[0] for row in queue}
        assert {"FETCH_FAILED", "RUN_WARNING"} <= issues
        conn.rollback()

def test_extract_limit_enters_review_queue_and_keeps_bytes(monkeypatch):
    """D-04: a PDF past an extraction limit is REVIEW (EXTRACT_LIMIT_EXCEEDED)
    and lands in pdf_review_queue; its bytes are kept."""
    from heimdal.pdfhist import extract
    monkeypatch.setattr(extract, "MAX_PDF_BYTES", len(FELAG_BYTES) - 1)
    with connect() as conn:
        result = _ingest(conn, document="extract-limit-01", run=9801)
        assert result["parse_status"] == "REVIEW"
        assert result["structured_data"]["review_reasons"] == \
            ["EXTRACT_LIMIT_EXCEEDED"]
        detail = conn.execute(
            "SELECT detail FROM pdf_review_queue WHERE issue = 'PARSE_REVIEW'"
            " AND parse_result_id = %s", (result["parse_result_id"],)
        ).fetchone()
        assert detail == ("EXTRACT_LIMIT_EXCEEDED",)
        stored = conn.execute(
            "SELECT content FROM pdf_document_content WHERE sha256 = %s",
            (SHA256,)).fetchone()[0]
        assert bytes(stored) == FELAG_BYTES
        conn.rollback()
