#!/usr/bin/env python3
"""Tests for Navigation Watchlist Counter and Source Reservation."""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI

def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_navigation_renders_watchlist_count(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Overvågning" in res.text

def test_navigation_renders_watchlist_count_badge(client):
    with conn() as c:
        c.execute("INSERT INTO user_watchlist (canonical_entity_id, entity_kind) VALUES (5708, 'COMPANY') ON CONFLICT DO NOTHING")
        c.commit()
    res = client.get("/")
    assert res.status_code == 200
    assert "watchlist-nav-count" in res.text

def test_source_reservation_disclaimer_present(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Skráseting Føroya" in res.text
    assert "under opbygning" in res.text or "forbehold" in res.text
