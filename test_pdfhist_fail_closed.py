"""PDF history / analytics fail-closed regressions (audit 2026-11 lane W3a).

F-1/D-01/D-11  flags off or projection unavailable -> pages 200, never 500,
               and no projection connection with the kill switch off
D-02           integration on + UI off -> no Skráseting data on any page
D-03           person history dedups before LIMIT (count/first date over all);
               company history LIMIT keeps the newest
F-8/D-12/C-4   one failing fail-closed read cannot poison the request
               connection; no dead Analyse tab without an ACTIVE build

Isolated: routes read the heimdal clone in HEIMDAL_READ_DB (skipped when it
is unreachable); the D-03 tests seed rolled-back rows in the throwaway
HEIMDAL_TEST_DB. The projection DSN is pinned to a dead port for routes.
"""
from datetime import date, timedelta

import psycopg
import pytest
from psycopg.pq import TransactionStatus

from heimdal.pdfhist import consumer, projection
from heimdal.pdfhist.testsupport import ensure_test_database

DEAD = "postgresql://w3a@127.0.0.1:1/none?connect_timeout=1"
BADGE = 'title="Skráseting Føroya'


@pytest.fixture(scope="module")
def portal():
    from heimdal.config import DB_URI
    try:
        with psycopg.connect(DB_URI, connect_timeout=3) as c:
            person, company = c.execute(
                "SELECT (SELECT person_id FROM v_role_relation ORDER BY 1 LIMIT 1),"
                " (SELECT company_id FROM v_role_relation ORDER BY 1 LIMIT 1)"
            ).fetchone()
    except psycopg.Error as exc:
        pytest.skip(f"no heimdal clone in HEIMDAL_READ_DB: {exc}")
    from heimdal.app import create_app
    app = create_app()
    app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return app, person, company


def _flags(monkeypatch, integration, ui, dsn=DEAD):
    monkeypatch.setenv("PDF_HISTORY_INTEGRATION_ENABLED", integration)
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", ui)
    if dsn is None:
        monkeypatch.delenv("HEIMDAL_PDFHIST_PROJECTION_DSN", raising=False)
    else:
        monkeypatch.setenv("HEIMDAL_PDFHIST_PROJECTION_DSN", dsn)


# --- F-1 / D-01 / D-11 -------------------------------------------------------

@pytest.mark.parametrize("integration,ui,dsn", [
    ("false", "false", None),   # kill switch off, DSN unset
    ("true", "true", None),     # flags on, DSN unset
    ("true", "true", DEAD),     # flags on, projection unreachable
])
def test_pages_never_500_without_projection(portal, monkeypatch,
                                            integration, ui, dsn):
    app, person, company = portal
    _flags(monkeypatch, integration, ui, dsn)
    client = app.test_client()
    for url in (f"/persons/{person}", f"/persons/{person}/history",
                f"/companies/{company}", f"/companies/{company}/history"):
        assert client.get(url).status_code == 200, url
    assert client.get(f"/companies/{company}/skraseting").status_code == 404


def test_flags_off_opens_no_projection_connection(portal, monkeypatch):
    app, person, company = portal
    _flags(monkeypatch, "false", "false")
    opened = []
    monkeypatch.setattr(consumer, "_read_only_conn",
                        lambda dsn: opened.append(dsn))
    client = app.test_client()
    html = client.get(f"/companies/{company}").get_data(as_text=True)
    client.get(f"/persons/{person}")
    client.get(f"/persons/{person}/history")
    client.get(f"/companies/{company}/history")
    assert opened == []
    # D-11: no "no dated registrations" claim behind a closed gate
    assert "Ingen daterede registreringer" not in html
    assert "Skráseting-historikken er ikke aktiv" in html


# --- D-02 --------------------------------------------------------------------

def _fake_events(calls):
    person_event = {"person_role_id": 1, "company_canonical_entity_id": None,
                    "event_type": "BOARD_CHANGE", "action": "APPOINTED",
                    "event_date": "2020-01-01", "role_title": "Nevndarlimur",
                    "source_document_id": 1, "source_event_id": 1,
                    "total_events": 1, "first_event_date": "2020-01-01"}
    company_events = [{"event_type": "NAME_CHANGE", "action": "CHANGED",
                       "entity_key": "x", "effective_date": None,
                       "registration_date": str(date(2020, 1, 1)
                                                - timedelta(days=i)),
                       "source_document_id": i, "source_event_id": i,
                       "authority_state": "HISTORICAL_EVENT_ONLY"}
                      for i in range(101)]

    def person_history(*a, **k):
        calls.append("person")
        return [dict(person_event)]

    def company_history(*a, **k):
        calls.append("company")
        return [dict(e) for e in company_events]
    return person_history, company_history


@pytest.mark.parametrize("ui_on", [False, True])
def test_ui_flag_gates_all_skraseting_history(portal, monkeypatch, ui_on):
    app, person, company = portal
    _flags(monkeypatch, "true", "false")
    calls = []
    person_history, company_history = _fake_events(calls)
    monkeypatch.setattr(consumer, "get_skraseting_person_history",
                        person_history)
    monkeypatch.setattr(consumer, "get_projected_company_history",
                        company_history)
    if ui_on:  # positive control: same pages with exposure allowed
        monkeypatch.setattr(consumer, "ui_exposure_allowed", lambda *a: True)
    client = app.test_client()
    pages = [client.get(u).get_data(as_text=True) for u in (
        f"/companies/{company}/history", f"/persons/{person}/history",
        f"/persons/{person}")]
    overview = "registrerede historiske hændelser (Skráseting"
    if ui_on:
        assert BADGE in pages[0] and BADGE in pages[1] and overview in pages[2]
        # D-03: the company page renders its truncation badge
        assert "Skráseting: de nyeste 100 registreringer vises" in pages[0]
    else:
        assert calls == []
        assert not any(BADGE in p for p in pages)
        assert overview not in pages[2]


# --- F-8 / D-12 / C-4 --------------------------------------------------------

def _missing(c, *_a):
    return c.execute("SELECT 1 FROM registry_analytics.w3a_missing").fetchall()


FAKE_BUILD = {"build_run_id": 1, "content_hash": "x", "built_at": None,
              "coverage": {}}


@pytest.mark.parametrize("failing", ["active_build", "pair_rows_for_person"])
def test_failed_read_does_not_poison_request_connection(portal, monkeypatch,
                                                         failing):
    from heimdal.modules.analytics import repository as arepo
    from heimdal.modules.shared.db import conn
    app, person, _company = portal
    monkeypatch.setenv("REGISTRY_ANALYTICS_ENABLED", "1")
    monkeypatch.setattr(arepo, "active_build", lambda c: FAKE_BUILD)
    monkeypatch.setattr(arepo, failing, _missing)  # raises or is swallowed
    with app.test_request_context():
        glob = app.jinja_env.globals
        glob["analytics_person_section"](person)
        assert conn().info.transaction_status != TransactionStatus.INERROR
        assert glob["network_preview_summary"](person, "PERSON") is not None


def test_no_dead_analyse_tab_without_active_build(portal, monkeypatch):
    from heimdal.modules.analytics import repository as arepo
    app, person, company = portal
    _flags(monkeypatch, "false", "false")
    monkeypatch.setenv("REGISTRY_ANALYTICS_ENABLED", "1")
    monkeypatch.setattr(arepo, "active_build", lambda c: None)
    client = app.test_client()
    for url in (f"/persons/{person}", f"/companies/{company}"):
        html = client.get(url).get_data(as_text=True)
        assert 'data-tab="analyse"' in html
        assert 'data-panel="analyse"' in html, url
        assert "Ingen aktiv analytics-build" in html, url


# --- D-03 (synthetic rows in the throwaway pdfhist test DB) -------------------

@pytest.fixture()
def proj():
    with psycopg.connect(ensure_test_database()) as c:
        projection.apply_migration(c)
        run = c.execute(
            "INSERT INTO registry_projection.projection_run"
            " (projection_version, status, code_version)"
            " VALUES ('w3a', 'ACTIVE', 'w3a') RETURNING projection_run_id"
        ).fetchone()[0]
        yield c, run
        c.rollback()


def test_person_history_dedups_before_limit(proj):
    c, run = proj
    c.execute("INSERT INTO registry_projection.company_current_state"
              " (projection_run_id, company_id, registration_number)"
              " VALUES (%s, 1, '10')", (run,))
    se = c.execute(
        "INSERT INTO pdf_source_event (company_id, requested_regnr,"
        " row_skrasnr, source_row_key, raw_text, page_no, row_ordinal)"
        " VALUES (1, '10', '10', 'w3a#1', 'x', 0, 1) RETURNING id").fetchone()[0]
    role = c.execute(
        "INSERT INTO registry_projection.company_person_role"
        " (projection_run_id, company_id, role_type, person_name,"
        "  entity_normalized, source_scope, source_event_id)"
        " VALUES (%s, 1, 'BOARD', 'W3a Person', 'w3a person',"
        "  'HISTORICAL_EVENT', %s) RETURNING id", (run, se)).fetchone()[0]
    c.execute(
        "INSERT INTO registry_projection.entity_link (projection_run_id,"
        " entity_type, source_entity_key, canonical_entity_id, person_role_id,"
        " link_status, link_method, link_code_version)"
        " VALUES (%s, 'PERSON', %s, 777, %s, 'LINKED',"
        "  'EXACT_MULTI_ATTRIBUTE', 'w3a')", (run, f"ROLE:{role}", role))
    days = [date(2000, 1, 1) + timedelta(days=30 * i) for i in range(151)]
    for i, d in enumerate(days):  # every event repeats in a second notice
        for doc in (i, 1000 + i):
            c.execute(
                "INSERT INTO registry_projection.company_history_event"
                " (projection_run_id, company_id, event_type, action,"
                "  entity_key, registration_date, source_event_id,"
                "  source_document_id)"
                " VALUES (%s, 1, 'BOARD_CHANGE', 'APPOINTED', 'w3a person',"
                "  %s, %s, %s)", (run, d, se, doc))
    events = consumer.get_skraseting_person_history(
        777, conn=c, integration_enabled=True, limit=101)
    shown = [e["event_date"] for e in events]
    assert len(events) == 101  # was 51: LIMIT ran before the dedup
    assert shown == [str(d) for d in sorted(days, reverse=True)[:101]]
    assert events[0]["total_events"] == 151
    assert events[0]["first_event_date"] == "2000-01-01"


def test_company_history_limit_keeps_newest(proj):
    c, run = proj
    c.execute("INSERT INTO registry_projection.company_current_state"
              " (projection_run_id, company_id, registration_number)"
              " VALUES (%s, 2, '20')", (run,))
    dated = []
    for i in range(115):  # 10 old effective-dated, 105 newer registrations
        eff = date(2001, 1, 1) + timedelta(days=i) if i < 10 else None
        reg = None if i < 10 else date(2010, 1, 1) + timedelta(days=i)
        dated.append(reg or eff)
        c.execute(
            "INSERT INTO registry_projection.company_history_event"
            " (projection_run_id, company_id, event_type, action, entity_key,"
            "  effective_date, registration_date, source_document_id)"
            " VALUES (%s, 2, 'NAME_CHANGE', 'CHANGED', %s, %s, %s, %s)",
            (run, f"k{i}", eff, reg, i))
    events = consumer.get_projected_company_history(
        "20", conn=c, integration_enabled=True, limit=101)
    got = [e["registration_date"] or e["effective_date"] for e in events]
    assert got == [str(d) for d in sorted(dated, reverse=True)[:101]]
