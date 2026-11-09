"""E2 founding origin end-to-end (isolated DB): parser -> value_json ->
both consumer payloads -> "ved stiftelse" on the company and person surfaces.

Uses the dedicated throwaway test database through heimdal.pdfhist.testsupport,
exactly like the registry-projection tests. No network, no live DB.
"""
import psycopg
import pytest
from psycopg.types.json import Json

from heimdal.pdfhist import consumer, projection
from heimdal.pdfhist.parse import parse_lysing_pdf
from heimdal.pdfhist.testsupport import ensure_test_database

RUN = 424245
COMPANY = 9001
REGNR = "9001"
CANONICAL_PERSON = 777001

FOUNDING_TEXT = (
    "12.03.2021\n"
    "Skrásetingar-nr.  P/F  9001,  P/F Founding Test, 100 Tórshavn.\n"
    "Stovningardato:  25-10-1977\n"
    "Nevnd:\n"
    "Anna Test, Vegur 1, 100 Tórshavn (nevndarformaður)\n"
    "Stjóri:\n"
    "Bjørt Test, Vegur 2, 100 Tórshavn (stjóri)\n"
    "Navn:  P/F Nýtt Navn\n"
    "Skráseting Føroya tann  12. mars 2021\n")


def _content(conn, token: str) -> str:
    import hashlib
    sha = hashlib.sha256(token.encode()).hexdigest()
    conn.execute(
        "INSERT INTO pdf_document_content (sha256, byte_length, content) "
        "VALUES (%s,%s,%s) ON CONFLICT (sha256) DO NOTHING",
        (sha, len(token), token.encode()))
    return sha


def _seed(conn) -> None:
    """Minimal VERIFIED Felag + founding Lysing evidence for one company."""
    notice = parse_lysing_pdf(FOUNDING_TEXT)
    assert [c.get("origin") for c in notice["changes"]] == \
        ["FOUNDING", "FOUNDING", None]
    snapshot = {
        "registration_number": REGNR, "company_name": "P/F Founding Test",
        "board": [{"name": "Anna Test", "role": "Nevndarformaður"}],
        "management": [{"name": "Bjørt Test", "role": "Stjóri"}],
        "snapshot_date": "2026-08-31",
    }
    felag_doc = conn.execute(
        "INSERT INTO pdf_document (pdf_type, source_document_id) "
        "VALUES ('FelagPdf', 'F9001') RETURNING id").fetchone()[0]
    lys_doc = conn.execute(
        "INSERT INTO pdf_document (pdf_type, source_document_id) "
        "VALUES ('LysingPdf', 'L9001') RETURNING id").fetchone()[0]
    felag_obs = conn.execute(
        "INSERT INTO pdf_document_observation (document_id, "
        "collection_run_id, fetched_at, document_snapshot_date, sha256, "
        "byte_length, extracted_text) "
        "VALUES (%s,%s,now(),'2026-08-31',%s,1,'snap') RETURNING id",
        (felag_doc, RUN, _content(conn, "felag-9001"))).fetchone()[0]
    lys_obs = conn.execute(
        "INSERT INTO pdf_document_observation (document_id, "
        "collection_run_id, fetched_at, sha256, byte_length, extracted_text) "
        "VALUES (%s,%s,now(),%s,1,'lys') RETURNING id",
        (lys_doc, RUN, _content(conn, "lys-9001"))).fetchone()[0]
    conn.execute(
        "INSERT INTO pdf_parse_result (document_observation_id, "
        "parser_version, document_kind, raw_text, structured_data, "
        "parse_status) VALUES (%s,'2','COMPANY_SNAPSHOT','snap',%s,'PARSED')",
        (felag_obs, Json(snapshot)))
    conn.execute(
        "INSERT INTO pdf_parse_result (document_observation_id, "
        "parser_version, document_kind, raw_text, structured_data, "
        "parse_status) VALUES (%s,'2','REGISTRATION_NOTICE',%s,%s,'PARSED')",
        (lys_obs, FOUNDING_TEXT, Json(notice)))
    event = conn.execute(
        "INSERT INTO pdf_source_event (company_id, requested_regnr, "
        "row_skrasnr, source_row_key, source_event_date, raw_text, page_no, "
        "row_ordinal) VALUES (%s,%s,%s,'k-9001-x1','2021-03-12','row',0,1) "
        "RETURNING id", (COMPANY, REGNR, REGNR)).fetchone()[0]
    for doc, role in ((felag_doc, "FELAG_CELL"), (lys_doc, "LYSING_CELL")):
        conn.execute(
            "INSERT INTO pdf_source_event_document (source_event_id, "
            "document_id, cell_role) VALUES (%s,%s,%s)",
            (event, doc, role))


@pytest.fixture(scope="module")
def seeded():
    dsn = ensure_test_database()
    with psycopg.connect(dsn) as conn:
        _seed(conn)
        run_id = projection.build_shadow(
            conn, projection_version="e2-founding",
            source_collection_run_id=RUN)
        projection.activate(conn, run_id)
        conn.commit()
        yield conn, run_id


def test_origin_reaches_value_json_and_company_payload(seeded):
    conn, run_id = seeded
    rows = conn.execute(
        "SELECT action, value_json->>'origin' "
        "FROM registry_projection.company_history_event "
        "WHERE projection_run_id=%s AND company_id=%s ORDER BY id",
        (run_id, COMPANY)).fetchall()
    assert rows == [("APPOINTED", "FOUNDING"), ("APPOINTED", "FOUNDING"),
                    ("CHANGED", None)]
    history = consumer.get_projected_company_history(
        REGNR, conn=conn, integration_enabled=True)
    founding = [e for e in history if e.get("origin") == "FOUNDING"]
    assert len(founding) == 2
    assert all(e["action"] == "APPOINTED" for e in founding)
    assert {str(e["effective_date"]) for e in founding} == {"1977-10-25"}
    artifact_warnings = conn.execute(
        "SELECT count(*) FROM registry_projection.projection_warning "
        "WHERE projection_run_id=%s AND warning_code='SOURCE_DATE_ARTIFACT'",
        (run_id,)).fetchone()[0]
    assert artifact_warnings == 0


def test_person_payload_and_both_page_labels(seeded):
    conn, run_id = seeded
    role_id = conn.execute(
        "SELECT id FROM registry_projection.company_person_role "
        "WHERE projection_run_id=%s AND source_scope='HISTORICAL_EVENT' "
        "AND role_type='BOARD' ORDER BY id LIMIT 1", (run_id,)).fetchone()[0]
    conn.execute(
        "INSERT INTO registry_projection.entity_link (projection_run_id, "
        "entity_type, source_entity_key, canonical_entity_id, "
        "registration_number, person_role_id, link_status, link_method, "
        "link_code_version) VALUES (%s,'PERSON',%s,%s,%s,%s,'LINKED',"
        "'EXACT_MULTI_ATTRIBUTE','test')",
        (run_id, f"ROLE:{role_id}", CANONICAL_PERSON, REGNR, role_id))
    conn.commit()

    person_history = consumer.get_skraseting_person_history(
        CANONICAL_PERSON, conn=conn, integration_enabled=True)
    assert person_history
    assert any(e.get("origin") == "FOUNDING"
               and e["action"] == "APPOINTED" for e in person_history)

    # the two page branches of the shared timeline producer
    from heimdal.modules.entities.routes import _merged_entity_timeline
    company_history = consumer.get_projected_company_history(
        REGNR, conn=conn, integration_enabled=True)
    company_timeline = _merged_entity_timeline([], company_history)
    person_timeline = _merged_entity_timeline([], person_history)
    for timeline in (company_timeline, person_timeline):
        assert {e["detail"] for e in timeline
                if e.get("origin") == "FOUNDING"} == \
            {"Tiltrådt ved stiftelse"}
    assert any(e.get("origin") == "FOUNDING"
               for e in company_timeline)
    assert any(e.get("origin") == "FOUNDING"
               for e in person_timeline)

    # the Skráseting company page renders the same entry through _event_view;
    # a 1977 founding date is source-supported, not a date artifact
    from heimdal.modules.skraseting.views import _event_view
    entry = next(e for e in company_history if e.get("origin") == "FOUNDING")
    view = _event_view(entry)
    assert "ved stiftelse" in view["action_label"]
    assert view["timeline_date"] == "1977-10-25"
    assert view["caveats"] == []


def test_parser_version_default_is_two():
    from heimdal.pdfhist.ingest import PARSER_VERSION_DEFAULT
    assert PARSER_VERSION_DEFAULT == "2"


def test_non_founding_events_stay_unlabelled():
    from heimdal.modules.entities.routes import _merged_entity_timeline
    timeline = _merged_entity_timeline([], [
        {"event_type": "BOARD_CHANGE", "action": "APPOINTED",
         "origin": None, "event_date": "2020-01-02", "entity_key": "x"},
        {"event_type": "BOARD_CHANGE", "action": "REMOVED",
         "origin": None, "event_date": "2020-01-01", "entity_key": "x"}])
    assert [e["detail"] for e in timeline] == ["Tiltrådt", "Fratrådt"]


def test_non_founding_change_keeps_old_value_json_shape(seeded):
    """Only a FOUNDING change adds origin; the old value_json shape stays."""
    conn, run_id = seeded
    row = conn.execute(
        "SELECT value_json FROM registry_projection.company_history_event "
        "WHERE projection_run_id=%s AND company_id=%s "
        "AND event_type='NAME_CHANGE'", (run_id, COMPANY)).fetchone()
    assert row is not None
    assert set(row[0]) == {"entity", "raw_text"}
    assert row[0]["entity"] == "P/F Nýtt Navn"


def test_non_founding_pre_1990_event_keeps_artifact_caveat():
    """The founding-date exemption must not hide a normal pre-1990 artifact."""
    from heimdal.modules.skraseting.views import (
        SOURCE_DATE_ARTIFACT_NOTE, _event_view)
    view = _event_view({
        "event_type": "BOARD_CHANGE", "action": "APPOINTED",
        "origin": None, "effective_date": "1977-10-25",
        "entity_key": "x"})
    assert view["timeline_date"] is None
    assert SOURCE_DATE_ARTIFACT_NOTE in view["caveats"]


def test_non_numeric_date_string_skips_the_artifact_caveat():
    """NEW-3: a malformed date must not raise in the view model."""
    from heimdal.modules.skraseting.views import _event_view
    view = _event_view({
        "event_type": "BOARD_CHANGE", "action": "APPOINTED",
        "origin": None, "effective_date": "ukendt", "entity_key": "x"})
    assert view["timeline_date"] == "ukendt"
    assert view["caveats"] == []


def _link_person_role(conn, run_id, role_id):
    conn.execute(
        "INSERT INTO registry_projection.entity_link (projection_run_id, "
        "entity_type, source_entity_key, canonical_entity_id, "
        "registration_number, person_role_id, link_status, link_method, "
        "link_code_version) VALUES (%s,'PERSON',%s,%s,%s,%s,'LINKED',"
        "'EXACT_MULTI_ATTRIBUTE','test') ON CONFLICT DO NOTHING",
        (run_id, f"ROLE:{role_id}", CANONICAL_PERSON, REGNR, role_id))


def test_person_history_keeps_rows_differing_only_by_origin(seeded):
    """NEW-2: origin is part of the person-history dedup key.

    Two history rows for the same person role and event differ only by
    value_json->>'origin'; both must survive the DISTINCT ON.
    """
    conn, run_id = seeded
    role = conn.execute(
        "SELECT id, company_id, entity_normalized, source_event_id, "
        "source_document_id FROM registry_projection.company_person_role "
        "WHERE projection_run_id=%s AND source_scope='HISTORICAL_EVENT' "
        "AND role_type='BOARD' ORDER BY id LIMIT 1", (run_id,)).fetchone()
    role_id, company_id, entity_norm, source_event_id, _doc_id = role
    _link_person_role(conn, run_id, role_id)
    # same role/event as the projected FOUNDING row but without an origin;
    # the unique key needs another source document id.
    dup_doc = conn.execute(
        "INSERT INTO pdf_document (pdf_type, source_document_id) "
        "VALUES ('LysingPdf','L9001-dup') RETURNING id").fetchone()[0]
    conn.execute(
        "INSERT INTO registry_projection.company_history_event "
        "(projection_run_id, company_id, registration_number, event_type, "
        "action, entity_key, value_json, effective_date, notice_date, "
        "source_event_date, source_event_id, source_document_id, raw_text, "
        "authority_state) VALUES (%s,%s,%s,'BOARD_CHANGE','APPOINTED',%s,%s,"
        "'1977-10-25','2021-03-12','2021-03-12',%s,%s,'dup',"
        "'HISTORICAL_EVENT_ONLY')",
        (run_id, company_id, REGNR, entity_norm,
         Json({"entity": "Anna Test", "raw_text": "dup"}),
         source_event_id, dup_doc))
    conn.commit()
    events = consumer.get_skraseting_person_history(
        CANONICAL_PERSON, conn=conn, integration_enabled=True)
    assert events is not None
    matching = [e for e in events
                if e["person_role_id"] == role_id
                and e["event_type"] == "BOARD_CHANGE"
                and e["action"] == "APPOINTED"]
    assert sorted(e["origin"] or "" for e in matching) == ["", "FOUNDING"]


def test_same_person_board_and_management_gets_each_event_once(seeded):
    """X5b review #7: match he.event_type to pr.role_type.

    One person in both Nevnd and Stjóri of one notice holds two projected
    roles and two history events. Without the role/event match each role
    joins BOTH events (4 rows, mislabelled); with it each gets its own."""
    conn, run_id = seeded
    board = conn.execute(
        "SELECT id, company_id, person_name, role_title, entity_normalized, "
        "source_document_id, source_observation_id, source_parse_result_id, "
        "source_event_id FROM registry_projection.company_person_role "
        "WHERE projection_run_id=%s AND source_scope='HISTORICAL_EVENT' "
        "AND role_type='BOARD' ORDER BY id LIMIT 1", (run_id,)).fetchone()
    (board_id, company_id, person_name, role_title, entity_norm, doc_id,
     observation_id, parse_id, event_id) = board
    # the same person now also holds a MANAGEMENT role in the same event
    management_id = conn.execute(
        "INSERT INTO registry_projection.company_person_role "
        "(projection_run_id, company_id, role_type, person_name, role_title, "
        " entity_normalized, source_scope, source_document_id, "
        " source_observation_id, source_parse_result_id, source_event_id) "
        "VALUES (%s,%s,'MANAGEMENT',%s,'Stjóri',%s,'HISTORICAL_EVENT',"
        "        %s,%s,%s,%s) RETURNING id",
        (run_id, company_id, person_name, entity_norm, doc_id,
         observation_id, parse_id, event_id)).fetchone()[0]
    # one MANAGEMENT_CHANGE history event for the same entity/event
    conn.execute(
        "INSERT INTO registry_projection.company_history_event "
        "(projection_run_id, company_id, registration_number, event_type, "
        "action, entity_key, value_json, effective_date, notice_date, "
        "source_event_date, source_event_id, source_document_id, "
        "source_observation_id, source_parse_result_id, raw_text, "
        "authority_state) "
        "SELECT projection_run_id, company_id, registration_number, "
        "       'MANAGEMENT_CHANGE', action, entity_key, value_json, "
        "       effective_date, notice_date, source_event_date, "
        "       source_event_id, source_document_id, source_observation_id, "
        "       source_parse_result_id, raw_text, authority_state "
        "FROM registry_projection.company_history_event "
        "WHERE projection_run_id=%s AND company_id=%s "
        "  AND event_type='BOARD_CHANGE' AND entity_key=%s "
        "ORDER BY id LIMIT 1",
        (run_id, company_id, entity_norm))
    _link_person_role(conn, run_id, board_id)
    _link_person_role(conn, run_id, management_id)
    conn.commit()

    events = consumer.get_skraseting_person_history(
        CANONICAL_PERSON, conn=conn, integration_enabled=True) or []
    # the earlier origin-dedup test added a second BOARD_CHANGE row for the
    # same event without an origin; this test is about the two founding
    # events of the notice.
    mine = [e for e in events
            if e["person_role_id"] in (board_id, management_id)
            and e["origin"] == "FOUNDING"]
    assert len(mine) == 2
    by_type = {e["event_type"]: e for e in mine}
    assert set(by_type) == {"BOARD_CHANGE", "MANAGEMENT_CHANGE"}
    assert by_type["BOARD_CHANGE"]["role_type"] == "BOARD"
    assert by_type["BOARD_CHANGE"]["role_title"] == role_title
    assert by_type["MANAGEMENT_CHANGE"]["role_type"] == "MANAGEMENT"
    assert by_type["MANAGEMENT_CHANGE"]["role_title"] == "Stjóri"


def test_board_change_reached_only_through_management_role_is_kept(seeded):
    """Fix round 1 #1: the event type labels the row, not the role row.

    ``company_person_role`` is unique without ``source_event_id``, so the
    projection can hold only a MANAGEMENT role for an event whose history is
    a BOARD_CHANGE appointment. The person history must still show that
    board event once, labelled from the event type, with no role title from
    the management row."""
    conn, run_id = seeded
    base = conn.execute(
        "SELECT company_id, source_document_id, source_observation_id, "
        "source_parse_result_id, source_event_id, entity_normalized "
        "FROM registry_projection.company_person_role "
        "WHERE projection_run_id=%s AND source_scope='HISTORICAL_EVENT' "
        "AND role_type='BOARD' ORDER BY id LIMIT 1", (run_id,)).fetchone()
    (company_id, doc_id, observation_id, parse_id, event_id,
     base_entity) = base
    management_id = conn.execute(
        "INSERT INTO registry_projection.company_person_role "
        "(projection_run_id, company_id, role_type, person_name, role_title, "
        " entity_normalized, source_scope, source_document_id, "
        " source_observation_id, source_parse_result_id, source_event_id) "
        "VALUES (%s,%s,'MANAGEMENT','Carla Test','Stjóri','carla test',"
        "        'HISTORICAL_EVENT',%s,%s,%s,%s) RETURNING id",
        (run_id, company_id, doc_id, observation_id, parse_id,
         event_id)).fetchone()[0]
    conn.execute(
        "INSERT INTO registry_projection.company_history_event "
        "(projection_run_id, company_id, registration_number, event_type, "
        " action, entity_key, value_json, effective_date, notice_date, "
        " source_event_date, source_event_id, source_document_id, "
        " source_observation_id, source_parse_result_id, raw_text, "
        " authority_state) "
        "SELECT projection_run_id, company_id, registration_number, "
        "       'BOARD_CHANGE', 'APPOINTED', 'carla test', value_json, "
        "       effective_date, notice_date, source_event_date, "
        "       source_event_id, source_document_id, source_observation_id, "
        "       source_parse_result_id, raw_text, authority_state "
        "FROM registry_projection.company_history_event "
        "WHERE projection_run_id=%s AND company_id=%s "
        "  AND event_type='BOARD_CHANGE' AND entity_key=%s "
        "ORDER BY id LIMIT 1",
        (run_id, company_id, base_entity))
    _link_person_role(conn, run_id, management_id)
    conn.commit()

    events = consumer.get_skraseting_person_history(
        CANONICAL_PERSON, conn=conn, integration_enabled=True) or []
    mine = [e for e in events if e["person_name"] == "Carla Test"]
    assert len(mine) == 1
    event = mine[0]
    assert event["event_type"] == "BOARD_CHANGE"
    assert event["role_type"] == "BOARD"
    assert event["action"] == "APPOINTED"
    assert event["role_title"] is None
    assert event["person_role_id"] == management_id
