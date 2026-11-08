"""View-level tests (plan FASE 15-17 + FASE 22): company_pdf_snapshot_history
and pdf_review_queue against the throwaway test database, using SYNTHETIC
snapshot PDFs (fixture_pdf.make_pdf) and the REAL fixture bytes.
Live HTTP requests executed: 0."""
import hashlib

from heimdal.pdfhist.fixture_pdf import make_pdf
from heimdal.pdfhist.ingest import ingest_pdf_observation
from heimdal.pdfhist.testsupport import connect, fixture_bytes, source_event_dict

RUN_A = 20001
RUN_B = 20002


def _snapshot_bytes(dagf: str, regnr: str = "5015",
                    name: str = "P/F Sjonskiftestestfelag") -> bytes:
    return make_pdf([
        f"Dagf. {dagf}",
        f"Skrasetingar-nr: {regnr}",
        f"Navn: {name}",
        "Adressa: Brekkutun 7",
        "100 Torshavn",
        "Nevnd: Nevndarformadur Bolv Rotarni",
        "Hagvegur 3, 100 Torshavn",
    ])


def _ingest_snap(conn, dagf, document, run=RUN_A, source=None,
                 parser_version="1", pdf_bytes=None, parser_fn=None):
    bytes_ = pdf_bytes or _snapshot_bytes(dagf, regnr="5015")
    event = source or source_event_dict(
        company_id=5015, row_skrasnr="5015",
        source_row_key="sg.vs.fo#5015",
        source_event_date="2024-06-01")
    return bytes_, ingest_pdf_observation(
        conn=conn, company_id=5015, source_event=event,
        pdf_type="FelagPdf", source_document_id=document,
        pdf_bytes=bytes_, collection_run_id=run,
        parser_version=parser_version, parser_fn=parser_fn)


def _history(conn, company=5015, documents=None):
    where = "WHERE h.company_id = %s"
    params: list = [company]
    if documents:
        where += (" AND h.document_observation_id IN (SELECT o.id FROM"
                  " pdf_document_observation o JOIN pdf_document d ON"
                  " d.id = o.document_id WHERE d.source_document_id = ANY(%s))")
        params.append(list(documents))
    rows = conn.execute(
        "SELECT h.company_id, h.parser_version, h.document_observation_id,"
        " h.valid_from, h.valid_to, h.sha256, h.snapshot_date_conflict"
        " FROM company_pdf_snapshot_history h " + where +
        " ORDER BY h.parser_version, h.valid_from, h.document_observation_id",
        params).fetchall()
    return [tuple(r) for r in rows]


def test_pdf_attr_01_matching_regnr_enters_history():
    with connect() as conn:
        _bytes, result = _ingest_snap(conn, "06-06-2020", "attr-01")
        assert result["parse_status"] == "PARSED"
        assert result["needs_review"] is False
        rows = _history(conn, documents=["attr-01"])
        assert len(rows) == 1
        assert rows[0][0] == 5015
        assert rows[0][3].isoformat() == "2020-06-06"
        assert rows[0][4] is None  # single snapshot: open interval
        assert rows[0][6] is False


def test_snapshot_01_to_04_intervals_with_exact_boundaries():
    with connect() as conn:
        _ingest_snap(conn, "06-06-2020", "snap-A")
        _ingest_snap(conn, "02-06-2021", "snap-B")
        _ingest_snap(conn, "17-03-2024", "snap-C")
        rows = _history(conn, documents=["snap-A", "snap-B", "snap-C"])
        assert [r[3].isoformat() for r in rows] == [
            "2020-06-06", "2021-06-02", "2024-03-17"]
        # [from, to) semantics: A closes exactly at B's start date; last open
        assert rows[0][4].isoformat() == "2021-06-02"
        assert rows[1][4].isoformat() == "2024-03-17"
        assert rows[2][4] is None
        # one interval per observation, none duplicated by the N:M junction
        assert len({r[2] for r in rows}) == 3
        assert not any(r[6] for r in rows)


def test_snapshot_dedup_01_two_row_sources_one_document_one_history_row():
    with connect() as conn:
        event1 = source_event_dict(company_id=5015, row_skrasnr="5015",
                                   source_row_key="sg.vs.fo#5015#row1",
                                   source_event_date="2024-06-01")
        event2 = source_event_dict(company_id=5015, row_skrasnr="5015",
                                   source_row_key="sg.vs.fo#5015#row2",
                                   source_event_date="2024-06-01")
        _ingest_snap(conn, "06-06-2020", "dedup-doc", source=event1)
        # same document + same run -> observation reused, junction grows to 2
        _ingest_snap(conn, "06-06-2020", "dedup-doc", source=event2)
        junctions = conn.execute(
            "SELECT count(*) FROM pdf_source_event_document sed JOIN"
            " pdf_document d ON d.id = sed.document_id WHERE"
            " d.source_document_id = 'dedup-doc'").fetchone()[0]
        assert junctions == 2
        # repeating the SAME source row changes nothing
        _ingest_snap(conn, "06-06-2020", "dedup-doc", source=event1)
        junctions = conn.execute(
            "SELECT count(*) FROM pdf_source_event_document sed JOIN"
            " pdf_document d ON d.id = sed.document_id WHERE"
            " d.source_document_id = 'dedup-doc'").fetchone()[0]
        assert junctions == 2
        # DISTINCT in the view: one document => exactly one history row
        dedup_rows = conn.execute(
            "SELECT count(*) FROM company_pdf_snapshot_history h WHERE"
            " h.document_observation_id IN (SELECT o.id FROM"
            " pdf_document_observation o JOIN pdf_document d ON"
            " d.id = o.document_id WHERE d.source_document_id = 'dedup-doc')"
        ).fetchone()[0]
        assert dedup_rows == 1


def test_snapshot_conflict_same_date_different_bytes_flagged_and_queued():
    name1 = "P/F Konfliktfelag 1"
    name2 = "P/K Konfliktfelag 2"
    with connect() as conn:
        bytes1 = _snapshot_bytes("10-10-2016", name=name1)
        bytes2 = make_pdf([
            "Dagf. 10-10-2016",
            "Skrasetingar-nr: 5015",
            f"Navn: {name2}",
            "Adressa: Annat vegur 9",
            "100 Torshavn",
        ])
        assert bytes1 != bytes2  # same snapshot date, different hash
        ingest_pdf_observation(
            conn=conn, company_id=5015,
            source_event=source_event_dict(
                company_id=5015, row_skrasnr="5015",
                source_row_key="sg.vs.fo#5015",
                source_event_date="2016-10-11"),
            pdf_type="FelagPdf", source_document_id="conflict-1",
            pdf_bytes=bytes1, collection_run_id=20010)
        ingest_pdf_observation(
            conn=conn, company_id=5015,
            source_event=source_event_dict(
                company_id=5015, row_skrasnr="5015",
                source_row_key="sg.vs.fo#5015",
                source_event_date="2016-10-11"),
            pdf_type="FelagPdf", source_document_id="conflict-2",
            pdf_bytes=bytes2, collection_run_id=20010)
        rows = [r for r in _history(conn) if r[3].isoformat() == "2016-10-10"]
        assert len(rows) == 2  # both kept as evidence, neither overridden
        assert all(r[6] for r in rows)  # conflict flag on both
        queued = conn.execute(
            "SELECT count(*) FROM pdf_review_queue WHERE issue ="
            " 'SNAPSHOT_DATE_CONFLICT' AND company_id = 5015"
        ).fetchone()[0]
        assert queued >= 1


def test_parser_tl_01_parser_versions_have_separate_timelines():
    # v2 understands an ISO-style Dagf. that v1 cannot parse (review).
    from datetime import date

    def v2_parser(text):
        from heimdal.pdfhist.parse import parse_felag_pdf
        parsed = parse_felag_pdf(text)
        if parsed["snapshot_date"] is None:
            import re
            match = re.search(r"Dagf\.\s+(\d{4})-(\d{2})-(\d{2})", text)
            if match:
                parsed["snapshot_date"] = "%s-%s-%s" % match.groups()
                parsed["needs_review"] = False
                parsed["review_reasons"] = []
        return parsed

    iso_text = ("Dagf. 2021-06-02\n"
                "Skrasetingar-nr: 5015\n"
                "Navn: P/F IsoDato felag\n"
                "Adressa: Vegur 2\n100 Torshavn\n")
    iso_bytes = make_pdf(iso_text.splitlines())  # a REAL PDF, not raw text
    with connect() as conn:
        _ingest_snap(conn, "06-06-2020", "tl-A", parser_version="1")
        _ingest_snap(conn, "02-06-2021", "tl-B", parser_version="1")
        _ingest_snap(conn, None, "tl-C", parser_version="1",
                     pdf_bytes=iso_bytes)
        _ingest_snap(conn, "06-06-2020", "tl-A", parser_version="2",
                     parser_fn=v2_parser)
        _ingest_snap(conn, "02-06-2021", "tl-B", parser_version="2",
                     parser_fn=v2_parser)
        _ingest_snap(conn, None, "tl-C", parser_version="2",
                     pdf_bytes=iso_bytes, parser_fn=v2_parser)
        parses = conn.execute(
            "SELECT p.parser_version, count(*) FROM pdf_parse_result p JOIN"
            " pdf_document_observation o ON o.id = p.document_observation_id"
            " JOIN pdf_document d ON d.id = o.document_id WHERE"
            " d.source_document_id IN ('tl-A','tl-B','tl-C') GROUP BY 1"
            " ORDER BY 1").fetchall()
        assert parses == [("1", 3), ("2", 3)]
        rows_v1 = conn.execute(
            "SELECT h.valid_from FROM company_pdf_snapshot_history h JOIN"
            " pdf_document_observation o ON o.id = h.document_observation_id"
            " JOIN pdf_document d ON d.id = o.document_id WHERE"
            " h.parser_version = '1' AND d.source_document_id = ANY(%s)"
            " ORDER BY h.valid_from", (["tl-A", "tl-B", "tl-C"],)).fetchall()
        rows_v2 = conn.execute(
            "SELECT h.valid_from FROM company_pdf_snapshot_history h JOIN"
            " pdf_document_observation o ON o.id = h.document_observation_id"
            " JOIN pdf_document d ON d.id = o.document_id WHERE"
            " h.parser_version = '2' AND d.source_document_id = ANY(%s)"
            " ORDER BY h.valid_from", (["tl-A", "tl-B", "tl-C"],)).fetchall()
        assert [r[0].isoformat() for r in rows_v1] == [
            "2020-06-06", "2021-06-02"]
        assert [r[0].isoformat() for r in rows_v2] == [
            "2020-06-06", "2021-06-02", "2021-06-02"]


def test_pdf_attr_02_row_and_pdf_claim_different_companies_fail_closed():
    # real corpus PDF states regnr 10, but the search row says 5019
    with connect() as conn:
        event = source_event_dict(company_id=5019, row_skrasnr="5019",
                                  source_row_key="sg.vs.fo#5019",
                                  source_event_date="2026-09-01")
        result = ingest_pdf_observation(
            conn=conn, company_id=5019, source_event=event,
            pdf_type="FelagPdf", source_document_id="attr-mismatch",
            pdf_bytes=fixture_bytes("felag_10.bin"),
            collection_run_id=20020)
        assert result["parse_status"] == "PARSED"  # evidence kept, not hidden
        history = conn.execute(
            "SELECT count(*) FROM company_pdf_snapshot_history WHERE"
            " company_id = 5019").fetchone()[0]
        assert history == 0  # fail-closed: nothing enters the timeline
        queue = conn.execute(
            "SELECT issue, detail FROM pdf_review_queue WHERE issue ="
            " 'PDF_COMPANY_MISMATCH' AND company_id = 5019").fetchall()
        assert len(queue) == 1
        assert "10" in queue[0][1] and "5019" in queue[0][1]


def test_pdf_attr_03_missing_regnr_goes_to_review_and_is_excluded():
    blank_regnr = make_pdf([
        "Dagf. 05-05-2018",
        "Navn: P/F Regnrlaust felag",
        "Adressa: Vegur 1",
        "100 Torshavn",
    ])
    with connect() as conn:
        ingest_pdf_observation(
            conn=conn, company_id=1234,
            source_event=source_event_dict(
                company_id=1234, row_skrasnr="1234",
                source_row_key="sg.vs.fo#1234",
                source_event_date="2018-05-06"),
            pdf_type="FelagPdf", source_document_id="attr-missing",
            pdf_bytes=blank_regnr, collection_run_id=20030)
        history = conn.execute(
            "SELECT count(*) FROM company_pdf_snapshot_history WHERE"
            " company_id = 1234").fetchone()[0]
        assert history == 0
        issues = {r[0] for r in conn.execute(
            "SELECT issue FROM pdf_review_queue WHERE company_id = 1234"
        ).fetchall()}
        assert "PDF_COMPANY_MISMATCH_MISSING_REGNR" in issues
        assert "PARSE_REVIEW" in issues  # REGISTRATION_NUMBER_MISSING review