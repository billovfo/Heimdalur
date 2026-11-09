"""X3b end-to-end proof (isolated DB): MERGER parser -> projection event ->
both consumer payloads and the page labels.

Synthetic names only. Throwaway test database through heimdal.pdfhist.testsupport,
exactly like test_pdfhist_founding_origin.py. No network, no live DB.
"""
import psycopg
import pytest
from psycopg.types.json import Json

from heimdal.pdfhist import consumer, projection
from heimdal.pdfhist.parse import parse_lysing_pdf
from heimdal.pdfhist.testsupport import ensure_test_database

RUN = 424246
COMPANY = 9201
REGNR = "9201"

PLAN_TEXT = (
    "12.03.2021\n"
    "Skrásetingar-nr.  P/F  9201,  P/F Test A, 100 Tórshavn.\n"
    "Fusión:  Skráseting Føroya hevur í dag móttikið fusiónsæ tlan "
    "samsv. § 134 a í P/F-lógini:\n"
    "A. Navn: P/F Test A (P/F-nr.9201), 100 Tórshavn\n"
    "B: Navn: P/F Test B (P/F-nr. 9202), 200 Klaksvík.\n"
    "Skráseting Føroya tann  12. mars 2021\n")

COMPLETED_TEXT = (
    "13.03.2021\n"
    "Skrásetingar-nr.  P/F  9201,  P/F Test A, 100 Tórshavn.\n"
    "Fusión:  Felagið er samanlagt við skrásetingar-nr. P/F 9203, "
    "P/F Test C, sum samstundis verður strikað.\n"
    "Skráseting Føroya tann  13. mars 2021\n")


def _content(conn, token: str) -> str:
    import hashlib
    sha = hashlib.sha256(token.encode()).hexdigest()
    conn.execute(
        "INSERT INTO pdf_document_content (sha256, byte_length, content) "
        "VALUES (%s,%s,%s) ON CONFLICT (sha256) DO NOTHING",
        (sha, len(token), token.encode()))
    return sha


def _seed_notice(conn, token: str, source_document_id: str, raw_text: str,
                 notice: dict) -> None:
    doc = conn.execute(
        "INSERT INTO pdf_document (pdf_type, source_document_id) "
        "VALUES ('LysingPdf', %s) RETURNING id",
        (source_document_id,)).fetchone()[0]
    obs = conn.execute(
        "INSERT INTO pdf_document_observation (document_id, "
        "collection_run_id, fetched_at, sha256, byte_length, extracted_text) "
        "VALUES (%s,%s,now(),%s,1,%s) RETURNING id",
        (doc, RUN, _content(conn, token), raw_text)).fetchone()[0]
    conn.execute(
        "INSERT INTO pdf_parse_result (document_observation_id, "
        "parser_version, document_kind, raw_text, structured_data, "
        "parse_status) VALUES (%s,'2','REGISTRATION_NOTICE',%s,%s,'PARSED')",
        (obs, raw_text, Json(notice)))
    event = conn.execute(
        "INSERT INTO pdf_source_event (company_id, requested_regnr, "
        "row_skrasnr, source_row_key, source_event_date, raw_text, page_no, "
        "row_ordinal) VALUES (%s,%s,%s,%s,'2021-03-12','row',0,1) "
        "RETURNING id",
        (COMPANY, REGNR, REGNR, f"k-{source_document_id}")).fetchone()[0]
    conn.execute(
        "INSERT INTO pdf_source_event_document (source_event_id, "
        "document_id, cell_role) VALUES (%s,%s,'LYSING_CELL')",
        (event, doc))


def _seed(conn) -> None:
    snapshot = {
        "registration_number": REGNR, "company_name": "P/F Test A",
        "board": [], "management": [], "snapshot_date": "2026-08-31",
    }
    felag_doc = conn.execute(
        "INSERT INTO pdf_document (pdf_type, source_document_id) "
        "VALUES ('FelagPdf', 'F9201') RETURNING id").fetchone()[0]
    felag_obs = conn.execute(
        "INSERT INTO pdf_document_observation (document_id, "
        "collection_run_id, fetched_at, document_snapshot_date, sha256, "
        "byte_length, extracted_text) "
        "VALUES (%s,%s,now(),'2026-08-31',%s,1,'snap') RETURNING id",
        (felag_doc, RUN, _content(conn, "felag-9201"))).fetchone()[0]
    conn.execute(
        "INSERT INTO pdf_parse_result (document_observation_id, "
        "parser_version, document_kind, raw_text, structured_data, "
        "parse_status) VALUES (%s,'2','COMPANY_SNAPSHOT','snap',%s,'PARSED')",
        (felag_obs, Json(snapshot)))
    felag_event = conn.execute(
        "INSERT INTO pdf_source_event (company_id, requested_regnr, "
        "row_skrasnr, source_row_key, source_event_date, raw_text, page_no, "
        "row_ordinal) VALUES (%s,%s,%s,'k-felag-9201','2021-03-12','row',0,1) "
        "RETURNING id", (COMPANY, REGNR, REGNR)).fetchone()[0]
    conn.execute(
        "INSERT INTO pdf_source_event_document (source_event_id, "
        "document_id, cell_role) VALUES (%s,%s,'FELAG_CELL')",
        (felag_event, felag_doc))
    _seed_notice(conn, "lys-plan-9201", "L9201-PLAN", PLAN_TEXT,
                 parse_lysing_pdf(PLAN_TEXT))
    _seed_notice(conn, "lys-completed-9201", "L9201-DONE", COMPLETED_TEXT,
                 parse_lysing_pdf(COMPLETED_TEXT))


@pytest.fixture(scope="module")
def seeded():
    dsn = ensure_test_database()
    with psycopg.connect(dsn) as conn:
        _seed(conn)
        run_id = projection.build_shadow(
            conn, projection_version="x3b-merger",
            source_collection_run_id=RUN)
        projection.activate(conn, run_id)
        conn.commit()
        yield conn, run_id


def test_merger_rows_reach_company_history_event(seeded):
    conn, run_id = seeded
    rows = conn.execute(
        "SELECT event_type, action, entity_key, value_json "
        "FROM registry_projection.company_history_event "
        "WHERE projection_run_id=%s AND company_id=%s "
        "AND event_type='MERGER' ORDER BY action",
        (run_id, COMPANY)).fetchall()
    assert [r[:3] for r in rows] == [
        ("MERGER", "COMPLETED", "9203"),
        ("MERGER", "PLAN_REGISTERED", "9202"),
    ]
    completed_value = rows[0][3]
    assert set(completed_value) == {"counterpart", "raw_text"}
    assert completed_value["counterpart"] == {
        "registration_number": "9203", "name": "P/F Test C"}
    plan_value = rows[1][3]
    assert set(plan_value) == {"parties", "raw_text"}
    assert [p["registration_number"] for p in plan_value["parties"]] == \
        ["9201", "9202"]
    assert "fusiónsæ tlan" in plan_value["raw_text"]


def test_merger_is_not_a_membership_role(seeded):
    conn, run_id = seeded
    count = conn.execute(
        "SELECT count(*) FROM registry_projection.company_person_role "
        "WHERE projection_run_id=%s AND source_scope='HISTORICAL_EVENT'",
        (run_id,)).fetchone()[0]
    assert count == 0


def test_company_consumer_payload_carries_merger_evidence(seeded):
    conn, _run_id = seeded
    history = consumer.get_projected_company_history(
        REGNR, conn=conn, integration_enabled=True)
    mergers = [e for e in history if e["event_type"] == "MERGER"]
    assert {e["action"] for e in mergers} == {"PLAN_REGISTERED", "COMPLETED"}
    plan = next(e for e in mergers if e["action"] == "PLAN_REGISTERED")
    assert plan["entity_key"] == "9202"
    assert plan["counterpart"]["registration_number"] == "9202"
    assert [p["side"] for p in plan["parties"]] == ["A", "B"]
    completed = next(e for e in mergers if e["action"] == "COMPLETED")
    assert completed["entity_key"] == "9203"
    assert completed["counterpart"] == {
        "registration_number": "9203", "name": "P/F Test C"}
    assert consumer.merger_counterpart(completed) == completed["counterpart"]


def test_entity_history_timeline_shows_counterpart(seeded):
    conn, _run_id = seeded
    from heimdal.modules.entities.routes import (
        SKRASETING_EVENT_LABELS, _merged_entity_timeline)
    history = consumer.get_projected_company_history(
        REGNR, conn=conn, integration_enabled=True)
    timeline = _merged_entity_timeline([], history)
    plan = next(e for e in timeline if e["action"] == "PLAN_REGISTERED")
    assert plan["title"] == "Fusion"
    assert "Fusionsplan modtaget" in plan["detail"]
    assert "P/F Test B" in plan["detail"]
    assert "(9202)" in plan["detail"]
    assert plan["counterpart_name"] == "P/F Test B"
    assert plan["counterpart_registration_number"] == "9202"
    completed = next(e for e in timeline if e["action"] == "COMPLETED")
    assert "Fusion gennemført" in completed["detail"]
    assert "P/F Test C (9203)" in completed["detail"]
    assert SKRASETING_EVENT_LABELS["MERGER"] == "Fusion"


def test_skraseting_event_view_labels_and_counterpart(seeded):
    conn, _run_id = seeded
    from heimdal.modules.skraseting.views import (
        MERGER_ACTION_LABELS, ROLE_TITLE_LABELS, _event_view)
    history = consumer.get_projected_company_history(
        REGNR, conn=conn, integration_enabled=True)
    plan = next(e for e in history if e["action"] == "PLAN_REGISTERED")
    view = _event_view(plan)
    assert view["type"] == "Fusion registreret"
    assert view["action_label"] == "Fusionsplan modtaget"
    assert view["description"] == "P/F Test B (9202)"
    assert view["counterpart_name"] == "P/F Test B"
    assert view["counterpart_registration_number"] == "9202"
    completed = next(e for e in history if e["action"] == "COMPLETED")
    assert _event_view(completed)["action_label"] == "Fusion gennemført"
    assert MERGER_ACTION_LABELS == {
        "PLAN_REGISTERED": "Fusionsplan modtaget",
        "COMPLETED": "Fusion gennemført"}
    assert ROLE_TITLE_LABELS["Likvidator"] == "Likvidator"


def test_parser_version_default_stays_two():
    from heimdal.pdfhist.ingest import PARSER_VERSION_DEFAULT
    assert PARSER_VERSION_DEFAULT == "2"
