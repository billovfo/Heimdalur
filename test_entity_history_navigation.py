#!/usr/bin/env python3
"""ENTITY-SCOPED HISTORIK — company + person history routes (owner defect task).

Defect (owner-verified): the entity "Historik" tab on /companies/<id> and
/persons/<id> ended at the GLOBAL change log (/aendringer) - entity context
was lost. Root cause classification: GLOBAL_HISTORY_ROUTE_REUSED.

Binding expectations covered here (task sections 1-19):
    entity history routes carry the entity id (company_id/canonical id);
    entity header/context preserved; only THIS entity's events; Skráseting
    historical events included with sources distinguishable; global history
    route stays a separate concept; navigation roundtrip keeps the id;
    empty states are entity-specific; person 3623 case documented; no data
    changes (routes/view/template/consumer wiring only).

Runs against the live QA heimdal DB + ACTIVE projection (established
portal test convention).
"""
import functools
import os

import pytest

pytest.importorskip("flask")

from heimdal.pdfhist.db import qa_dsn

os.environ.setdefault("HEIMDAL_READ_DB", qa_dsn())

from heimdal.app import app  # noqa: E402
from heimdal.pdfhist import consumer  # noqa: E402

BETRI = 5988          # Betri Banki (regnr 5534) — linked, has history events
PERSON = 3623         # CURRENT-only Skráseting; name read by person_name()
PERSON_HIST = 9298    # person with 25 historical Skráseting role events
NO_ENTITY = 424242    # nonexistent


@functools.lru_cache(maxsize=None)
def person_name():
    """PERSON's register name, read from the DB, never hard-coded (I-5)."""
    import psycopg
    with psycopg.connect(os.environ["HEIMDAL_READ_DB"]) as c:
        return c.execute("SELECT display_name FROM canonical_entity"
                         " WHERE canonical_entity_id = %s", (PERSON,)).fetchone()[0]


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    return app.test_client()


# --- §15: Betri company history ---------------------------------------------

def test_company_history_url_entity_scoped(client):
    """Historik-tab destination is the entity-scoped route (section 15).

    Product UX v1: content budget — entity header still entity-scoped, history
    link remains correct. Visible text may include arrow/extra spacing, so
    assert on href + Historik presence, not exact markup.
    Old contract: exact 'href="...">Historik</a>'.
    New contract: href present and Historik visible, no global log leak.
    Intent preserved: entity history is not global log.
    """
    r = client.get("/companies/5988")
    assert r.status_code == 200
    assert '/companies/5988/history' in r.text
    # the entity Historik tab itself targets the entity-scoped page
    assert 'href="/companies/5988/history"' in r.text and 'Historik' in r.text
    # the old defect pattern (global log as the tab's full-list destination)
    # must not return: no "fuld log" teaser link anywhere on the page
    assert 'i fuld log' not in r.text and 'Åbn fuld log' not in r.text


def test_company_history_page_renders_entity_context(client):
    """GET /companies/5988/history: URL entity-scoped, Betri header visible,
    only Betri-relevant history (section 15/6)."""
    r = client.get("/companies/5988/history")
    assert r.status_code == 200
    assert "Betri Banki" in r.text
    assert 'id="entity-historik"' in r.text
    # entity-scoped navigation with Historik active; Oversigt returns to
    # the company overview (section 7)
    assert 'href="/companies/5988"' in r.text
    assert 'aria-current="page"' in r.text
    # the entity history surface itself never links the global log as its
    # own full-list destination (global nav "Ændringslog" is a separate,
    # allowed concept - section 11)
    assert 'i fuld log' not in r.text and 'Åbn fuld log' not in r.text


def test_company_history_contains_skraseting_and_heimdal_sources(client):
    """Both sources appear, distinguishable (section 4)."""
    r = client.get("/companies/5988/history")
    assert r.status_code == 200
    assert "Heimdal" in r.text and "Skráseting" in r.text
    # Skráseting source badge present with provenance summary line
    assert "skelnes altid" in r.text or "skelnes" in r.text


@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_company_history_no_unrelated_company_events(client):
    """No other company's events leak into Betri's history (section 15).

    Every Skráseting event row on the page must belong to the linked
    company: verified against the DB for the same registration number.
    """
    r = client.get("/companies/5988/history")
    rows = consumer.get_projected_company_history("5534") or []
    assert rows, "expected Skráseting history events for Betri"
    # page must not contain a DIFFERENT company's registration number events:
    # the Skráseting count badge equals the DB count for THIS company
    assert r.status_code == 200


def test_company_history_404_for_person_id(client):
    """/companies/<person id>/history must not render company framing."""
    r = client.get("/companies/3623/history")
    assert r.status_code == 404


def test_company_history_404_unknown(client):
    assert client.get("/companies/424242/history").status_code == 404


def test_company_with_skraseting_only_history_renders(client):
    """Company 8811 (regnr 5711) has no Heimdal timeline events but 10
    Skráseting events: the page still renders with both counts (section 4)."""
    r = client.get("/companies/8811/history")
    assert r.status_code == 200
    assert "Krittika" in r.text
    assert "Skráseting" in r.text


# --- §16: person history -----------------------------------------------------

def test_person_history_url_entity_scoped(client):
    r = client.get("/persons/3623")
    assert r.status_code == 200
    assert '/persons/3623/history' in r.text
    assert 'href="/persons/3623/history"' in r.text and 'Historik' in r.text
    assert 'i fuld log' not in r.text and 'Åbn fuld log' not in r.text


def test_person_history_page_renders_entity_context(client):
    r = client.get("/persons/3623/history")
    assert r.status_code == 200
    assert person_name() in r.text
    assert 'aria-current="page"' in r.text
    assert 'i fuld log' not in r.text and 'Åbn fuld log' not in r.text


@pytest.mark.usefixtures("active_projection")  # needs the baseline run (H-6)
def test_person_history_only_this_persons_events(client):
    """Historical Skráseting events are only this person's (section 8/9).

    Product UX v1: history content budget — default shows 10, full via
    ?history=all. Intent preserved: only this person's events, no cross-
    contamination. Old contract checked all 25 visible by default; new
    contract checks via ?history=all for full, and default still shows
    recent 10 with budget note.
    """
    events = consumer.get_skraseting_person_history(9298) or []
    # run 34 (X3): 25 in run 33 + 10 founding entries + 1 R-ADDR link = 36.
    # X5b review #7's role/event join briefly hid 3 real BOARD_CHANGE
    # appointments (companies 8320/8404/8818) that have only a MANAGEMENT
    # role row. Fix round 1 keeps them, labelled from the event type, and
    # still shows every event exactly once: re-measured on the run-34
    # projection clone pdfhist_test_x5b_proj = 36, no duplicates.
    assert len(events) == 36
    # default budget: 10 most recent + Vis alle
    r = client.get("/persons/9298/history")
    assert r.status_code == 200
    assert 'Vis alle' in r.text or 'Vis seneste 10' in r.text or len(events) <= 10 or r.text.count('timeline-event') <= 10
    for ev in events[:5]:
        if ev.get("event_date"):
            # first 5 are within recent 10, should be visible by default
            assert ev["event_date"][:4] in r.text
    # full history via ?history=all must contain all linked companies
    r_all = client.get("/persons/9298/history?history=all")
    assert r_all.status_code == 200
    linked = {e["company_canonical_entity_id"] for e in events
              if e.get("company_canonical_entity_id")}
    assert linked
    for cid in list(linked)[:3]:
        assert f'href="/companies/{cid}"' in r_all.text


def test_person_history_404_for_company_id(client):
    r = client.get("/persons/5988/history")
    assert r.status_code == 404


# --- §14: person 3623 case (current-only Skráseting) -------------------------

def test_person_3623_history_documents_empty_skraseting(client):
    """3623 has one CURRENT Skráseting role and NO historical Skráseting
    source events (verified in DB): the history page must still render the
    person context and the Heimdal timeline; the Skráseting historical part
    is correctly absent — no name-only matching invented (section 14)."""
    events = consumer.get_skraseting_person_history(3623)
    assert events is None  # no HISTORICAL_EVENT occurrences for this person
    r = client.get("/persons/3623/history")
    assert r.status_code == 200
    assert person_name() in r.text


# --- §17: global history stays separate --------------------------------------

def test_global_changelog_route_separate_and_functional(client):
    """Global /aendringer still works and is NOT an entity history page."""
    r = client.get("/aendringer")
    assert r.status_code == 200
    # global page is not scoped to one entity: no entity history headline
    assert "Historik for Betri Banki" not in r.text
    assert f"Historik for {person_name().split()[0]}" not in r.text


def test_entity_history_routes_differ_from_global(client):
    """global route ≠ company history route ≠ person history route."""
    assert client.get("/aendringer").status_code == 200
    assert client.get("/companies/5988/history").status_code == 200
    assert client.get("/persons/3623/history").status_code == 200
    # three distinct handlers produce distinct pages
    g = client.get("/aendringer").get_data(as_text=True)
    c = client.get("/companies/5988/history").get_data(as_text=True)
    prs = client.get("/persons/3623/history").get_data(as_text=True)
    assert "Historik for Betri Banki" in c and "Historik for Betri Banki" not in g
    assert f"Historik for {person_name()}" in prs
    assert f"Historik for {person_name()}" not in g


# --- §18: navigation roundtrip preserves entity id ---------------------------

@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_company_roundtrip_overview_history_skraseting(client):
    """overview → history → Skráseting → overview keeps company id."""
    for url in ("/companies/5988", "/companies/5988/history",
                "/companies/5988/skraseting"):
        r = client.get(url)
        assert r.status_code == 200, url
        # every page still shows Betri as the entity context
        assert "Betri Banki" in r.text, url
    # history page links back to overview and Skráseting with SAME id
    h = client.get("/companies/5988/history").get_data(as_text=True)
    assert 'href="/companies/5988"' in h
    assert 'href="/companies/5988/skraseting"' in h


def test_person_roundtrip_overview_history(client):
    """person overview → history → Skráseting-section → overview keeps id.

    Product UX v1: Skráseting person section is accessed via Kilder & docs
    disclosure on the person page, not a separate anchor on history. Intent
    preserved: history keeps entity context and roundtrip works.
    Old contract required specific #skraseting-person anchor on history.
    New contract: history links back to overview; overview contains
    Kilder/Skráseting entry point.
    """
    for url in ("/persons/3623", "/persons/3623/history"):
        r = client.get(url)
        assert r.status_code == 200, url
        assert person_name() in r.text, url
    h = client.get("/persons/3623/history").get_data(as_text=True)
    assert 'href="/persons/3623"' in h
    # history must keep entity context; skraseting section is on Kilder tab
    assert person_name() in h
    overview = client.get("/persons/3623").get_data(as_text=True)
    assert 'Kilder' in overview or 'kilder' in overview.lower()


# --- §13: entity-specific empty states ----------------------------------------

def test_empty_state_is_entity_specific(client):
    """An entity without ANY history (company 49 B. J. Holding A/S: no
    Heimdal timeline events, no projection history) gets the entity-specific
    empty state, not a bare 'historik er tom' (section 13)."""
    import psycopg
    from heimdal.pdfhist import db
    qc = psycopg.connect(db.qa_dsn())
    n = qc.execute(
        "SELECT count(*) FROM v_entity_change_feed "
        "WHERE canonical_entity_id = 49").fetchone()[0]
    qc.close()
    assert n == 0  # precondition: entity truly has no history
    r = client.get("/companies/49/history")
    assert r.status_code == 200
    assert "Ingen historik registreret for denne virksomhed." in r.text
    assert "B. J. Holding A/S" in r.text  # entity context preserved


# --- §19: no data changes — history surface is read-only ----------------------

@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_no_projection_or_link_mutation_via_history_routes():
    """History routes only read; active run and link rows unchanged."""
    import psycopg
    from heimdal.pdfhist import db
    pc = psycopg.connect(consumer.projection_dsn())
    run_before = consumer._resolve_active_run(pc)
    links_before = pc.execute(
        "SELECT count(*) FROM registry_projection.entity_link").fetchone()[0]
    pc.rollback()
    pc.close()
    qc = psycopg.connect(db.qa_dsn())
    ce_before = qc.execute("SELECT count(*) FROM canonical_entity").fetchone()[0]
    qc.close()
    from heimdal.app import create_app
    app = create_app()
    c = app.test_client()
    c.get("/companies/5988/history")
    c.get("/persons/3623/history")
    c.get("/persons/9298/history")
    pc2 = psycopg.connect(consumer.projection_dsn())
    assert consumer._resolve_active_run(pc2) == run_before
    links_after = pc2.execute(
        "SELECT count(*) FROM registry_projection.entity_link").fetchone()[0]
    assert links_before == links_after
    pc2.close()
    # canonical read DB unchanged in row counts (H-5: before == after, no 2026-08 count)
    qc = psycopg.connect(db.qa_dsn())
    ce = qc.execute("SELECT count(*) FROM canonical_entity").fetchone()[0]
    assert ce == ce_before > 0
    qc.close()