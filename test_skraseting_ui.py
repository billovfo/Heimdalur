from heimdal.pdfhist.db import qa_dsn
#!/usr/bin/env python3
"""Skráseting / PDF-historik UI v1 — shadow-mode verification (§42-§51).

Runs against the live QA heimdal DB + ACTIVE projection (established portal
test convention). The UI flags are driven by environment overrides
(flags.py env precedence) — the real runtime mirror stays untouched:
integration stays true, UI stays false outside these tests.

Known canonical fixtures (resolved live from company_inventory, verified
against the pilot projection):
    5988 Betri Banki (5534)   8811 Krittika u. tvingsilsavtøku (5711)
    2585 Knýta (8083)         6658 JPR Kranakoyring (6869)
    5803 Nordic Aquaculture Contractors (7403)
    8095 Landingarmiðstøðin á Toftum (6288)
    279  Formula (1062)       6923 Askham Smíð (8742)
    5600 Smyrjibreyðssmáttan (6692 — no projection)
    1426 Cosmus (9113 — no projection)
"""
import os

import pytest

pytest.importorskip("flask")

os.environ.setdefault("HEIMDAL_READ_DB",
                      qa_dsn())

from heimdal.app import app  # noqa: E402  (app creation after env default)

UI_ENV = {"PDF_HISTORY_UI_ENABLED": "true"}


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    return app.test_client()


# --- §42: UI flag off -> no entry, no rendering ------------------------------

def test_ui_flag_off_section_absent(client, monkeypatch):
    """ui=false -> sub-route 404s and the company page carries no entry.

    Forces the flag OFF via env (env overrides the DB mirror) so the test
    holds both before and after owner UI activation.
    """
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", "false")
    r = client.get("/companies/5988/skraseting")
    assert r.status_code == 404  # fail closed: no DOM-hidden data at all
    html = client.get("/companies/5988").get_data(as_text=True)
    assert "Skráseting / PDF-historik" not in html


# --- §43: integration off + UI on -> still no exposure -----------------------

def test_integration_off_ui_on_no_exposure(client, monkeypatch):
    monkeypatch.setenv("PDF_HISTORY_INTEGRATION_ENABLED", "false")
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", "true")
    assert client.get("/companies/5988/skraseting").status_code == 404
    html = client.get("/companies/5988").get_data(as_text=True)
    assert "Skráseting / PDF-historik" not in html


# --- §44/§11: both true -> section visible, reads ACTIVE projection ----------

@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_both_true_renders_projection(client, monkeypatch):
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", "true")
    html = client.get("/companies/5988/skraseting").get_data(as_text=True)
    assert "Registreret status pr." in html
    assert "Kilde: Skráseting Føroya" in html
    assert "P/F Betri Banki" in html  # registered name from the snapshot
    assert "Registreret navn ifølge Skráseting" in html
    assert "Historik" in html
    # the separate section must not claim it changed Heimdals data
    assert "ikke ændret" in html


# --- §45: current vs history separation --------------------------------------

@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_current_history_separation(client, monkeypatch):
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", "true")
    from heimdal.modules.shared.db import conn
    with app.app_context(), conn() as c:
        from heimdal.modules.skraseting import views
        view = views.skraseting_view(c, 5988)
        assert view["allowed"] and view["has_projection"]
    # current board comes from the snapshot view, not from history events
    history_types = {e["type"] for e in view["history"]}
    assert "Bestyrelsesændring registreret" in history_types  # UX-05 wording
    # ...and the current-state rendering is built only from the projection
    # fields, independently of the history list
    assert view["board"]  # current board present
    assert all(member["name"] for member in view["board"])


# --- §46: auditor never a factual current value; history may render it -------

@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_auditor_not_current_fact(client, monkeypatch):
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", "true")
    html = client.get("/companies/5988/skraseting").get_data(as_text=True)
    assert "Aktuel revisor kan ikke fastslås sikkert fra denne kilde" in html
    assert "Revisor: Ingen" not in html
    from heimdal.modules.shared.db import conn
    with app.app_context(), conn() as c:
        from heimdal.modules.skraseting import views
        view = views.skraseting_view(c, 5988)
        assert view["auditor_shown_as_current_value"] is False
    # historical auditor events still render (5534 has AUDITOR_CHANGE events)
    with app.app_context(), conn() as c:
        from heimdal.modules.skraseting import views
        view2 = views.skraseting_view(c, 5988)
        assert any(e["type"] == "Revisorændring registreret"
                   for e in view2["history"])
        # ...without any historical event mutating the current rendering
        assert view2["auditor_shown_as_current_value"] is False


# --- §47: company_form never claimed from the PDF panel ----------------------

@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_company_form_not_claimed(client, monkeypatch):
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", "true")
    html = client.get("/companies/5988/skraseting").get_data(as_text=True)
    # the explicit disclaimer is present; no company_form field rendered
    assert "ikke fra denne PDF-projektion" in html
    assert "Virksomhedstype" not in html.split("source-details")[0]


# --- §48: no-projection -> safe empty state ----------------------------------

@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
@pytest.mark.parametrize("cid", [5600, 1426])
def test_no_projection_empty_state(client, monkeypatch, cid):
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", "true")
    r = client.get(f"/companies/{cid}/skraseting")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Der er ingen autoritativ Skráseting-PDF-status" in html
    assert "Registreret status pr." not in html  # no blank pseudo-state
    # existing company page still works
    assert client.get(f"/companies/{cid}").status_code == 200


# --- §49: projection failure -> local error state ----------------------------

@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_projection_failure_local_error(client, monkeypatch):
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", "true")
    from heimdal.modules.skraseting import routes
    orig = routes.views.skraseting_view

    def boom(*a, **k):
        raise RuntimeError("simulated projection outage")

    monkeypatch.setattr(routes.views, "skraseting_view", boom)
    try:
        r = client.get("/companies/5988/skraseting")
    finally:
        monkeypatch.setattr(routes.views, "skraseting_view", orig, raising=False)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Skráseting-data kunne ikke indlæses" in html
    # the existing company page is unaffected
    assert client.get("/companies/5988").status_code == 200


# --- §50: provenance details accessible --------------------------------------

@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_provenance_details(client, monkeypatch):
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", "true")
    html = client.get("/companies/5988/skraseting").get_data(as_text=True)
    assert "Vis kildeoplysninger" in html
    assert "Dokument-id" in html and "Observation-id" in html
    assert "Parse-id" in html and "Snapshotdato" in html
    assert "AUTHORITATIVE_SNAPSHOT" in html


# --- §51: known name cases render projection value, canonical unchanged ------

@pytest.mark.parametrize("cid, projected", [
    (8811, "Sp/F Krittika í konkurs"),        # 5711 legal-status
    (2585, "ÍVF Hissini"),                    # 8083 source-fresher
    (6658, "Sp/F JPR Kranakoyring í likvidatión"),  # 6869
    (5803, "Í/F Nordic Aquaculture Contractors [NAC]"),  # 7403 alias
])
@pytest.mark.usefixtures("active_projection")  # needs run 33 (H-6)
def test_known_name_cases(client, monkeypatch, cid, projected):
    monkeypatch.setenv("PDF_HISTORY_UI_ENABLED", "true")
    html = client.get(f"/companies/{cid}/skraseting").get_data(as_text=True)
    assert projected in html  # rendered source-faithfully in Skráseting
    # canonical company page remains untouched
    ch = client.get(f"/companies/{cid}").get_data(as_text=True)
    assert "Skráseting-PDF-status" not in ch