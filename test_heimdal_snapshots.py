#!/usr/bin/env python3
"""Tests for Multi-Snapshot Diffing and Change Tracking."""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI
from heimdal.modules.snapshots.service import entity_change_feed, entity_timeline

TEST_COMPANY_WITH_ROLES = 5708
TEST_PERSON_FULL = 5761

def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_change_event_table_exists():
    with conn() as c:
        row = c.execute("SELECT count(*) AS n FROM entity_change_event").fetchone()
        assert row is not None

def test_entity_change_feed_query():
    with conn() as c:
        feed = entity_change_feed(c, limit=10)
        assert isinstance(feed, list)
        assert len(feed) <= 10
        if feed:
            event = feed[0]
            assert "event_id" in event
            assert "canonical_entity_id" in event
            assert "change_category" in event
            assert "change_summary" in event

def test_entity_timeline_diffing():
    with conn() as c:
        timeline = entity_timeline(c, TEST_COMPANY_WITH_ROLES)
        assert isinstance(timeline, list)

def test_changelog_page_renders(client):
    res = client.get("/aendringer")
    assert res.status_code == 200
    assert "Historisk Ændringslog" in res.text
    assert "Multi-Snapshot" in res.text

def test_company_page_displays_timeline(client):
    res = client.get(f"/companies/{TEST_COMPANY_WITH_ROLES}")
    assert res.status_code == 200
    assert "Historik" in res.text or "Ændringslog" in res.text

def test_changelog_date_filtering(client):
    res = client.get("/aendringer?from_date=2026-01-01&to_date=2026-12-31")
    assert res.status_code == 200
    assert "Historisk Ændringslog" in res.text
    assert "Datointerval" in res.text or "Fra dato" in res.text or "from_date" in res.text

