#!/usr/bin/env python3
"""Phase 2 (T2.1–T2.5) server-side detectors — remediation plan v1.

Run:  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 HEIMDAL_READ_DB=... \
      python -m pytest test_remediation_phase2.py -q
"""
import pytest
from heimdal.app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_t21_nav_contains_pathfinder(client):
    res = client.get("/")
    assert res.status_code == 200
    assert 'href="/find-forbindelse"' in res.text
    assert "Find forbindelse" in res.text


def test_t24_paadenne_side_removed(client):
    for route in ["/companies/9667", "/persons/7412",
                  "/companies/9667/network", "/persons/7412/network"]:
        res = client.get(route)
        assert res.status_code == 200, route
        assert "På denne side" not in res.text, route


def test_t25_beregnet_stamp_is_link_on_entity_page(client):
    res = client.get("/companies/9667")
    assert res.status_code == 200
    assert '<a href="/kildeinformation/9667" class="stamp st-beregnet stamp-link"' in res.text
    # the old untruthful click-promise tooltip is gone
    assert "klik Kildeinformation for detaljer" not in res.text


def test_t25_stamp_without_entity_has_no_click_promise(client):
    res = client.get("/")
    assert res.status_code == 200
    assert 'class="stamp st-beregnet"' in res.text          # span (no link)
    assert '<a href="/kildeinformation' not in res.text      # dashboard has no entity
    assert "klik Kildeinformation" not in res.text


def test_t25_no_circular_kilder_selflink(client):
    res = client.get("/companies/9667")
    assert res.status_code == 200
    assert 'href="#kilder-dok"' not in res.text
    assert "Vis i denne side" not in res.text


def test_t25_person_kilder_has_direct_sourceinfo_entry(client):
    res = client.get("/persons/7412")
    assert res.status_code == 200
    assert 'href="/kildeinformation/7412"' in res.text


def _tablist(html):
    """The page's tab strip: `<nav class="tablist">` on history pages, and on the
    entity page the `<div class="tablist">` whose role=tablist child holds only the
    tab buttons, with the Historik link beside it (W5d a11y, G-06)."""
    import re
    # company page: <div class="tablist"> (tabs + Historik link, W5d); history
    # page: <nav class="tablist">. Both close right before the tablist-wrap.
    m = re.search(r'class="tablist"[^>]*>(.*?)</(?:nav|div)>\s*</div>', html, re.S)
    return m.group(1) if m else ""


def test_t23_history_anchor_not_role_tab(client):
    res = client.get("/companies/9667")
    assert res.status_code == 200
    tablist = _tablist(res.text)
    assert 'data-route-tab="historik"' in tablist
    assert '<a role="tab"' not in tablist
    # history page tablist is a plain link nav (no tab roles at all)
    res = client.get("/companies/9667/history")
    assert res.status_code == 200
    tablist = _tablist(res.text)
    assert 'role="tab"' not in tablist
    assert "tab-route-link" in tablist