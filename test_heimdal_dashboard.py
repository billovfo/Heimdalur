#!/usr/bin/env python3
"""Tests for Heimdal Dashboard Modernization & Macro Intelligence Hub."""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI
from heimdal.modules.dashboard.service import dashboard_macro_stats, top_lists

def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_dashboard_macro_stats():
    with conn() as c:
        stats = dashboard_macro_stats(c)
        assert "total_companies" in stats
        assert "total_persons" in stats
        assert "total_relations" in stats
        assert "total_groups" in stats
        assert "total_observed_companies" in stats
        assert "total_aml_flags" not in stats
        assert stats["total_companies"] > 0
        assert stats["total_persons"] > 0
        assert stats["total_relations"] > 0

def test_top_lists_contains_groups_and_recent_events():
    with conn() as c:
        tops = top_lists(c)
        assert "groups" in tops
        assert "recent_events" in tops
        assert isinstance(tops["groups"], list)
        assert isinstance(tops["recent_events"], list)

def test_dashboard_renders_all_executive_zones(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Skráseting Føroya" in res.text
    assert "Aktive selskaber" in res.text
    assert "Ejerrelationer" in res.text
    assert "Graf-Stifinder" in res.text
    assert "Registerobservationer" in res.text
    assert "Koncerner" in res.text

def test_companies_filter_by_ownership_band(client):
    res = client.get("/companies?ownership_band=25-49")
    assert res.status_code == 200
    assert "Virksomheder" in res.text

def test_companies_filter_by_kind(client):
    res = client.get("/companies?kind=COMPANY")
    assert res.status_code == 200
    assert "Virksomheder" in res.text

def test_dashboard_charts_have_clickable_drilldown_links(client):
    res = client.get("/")
    assert res.status_code == 200
    assert 'href="/companies?ownership_band=' in res.text
    assert 'href="/companies?kind=' in res.text or 'href="/persons' in res.text




