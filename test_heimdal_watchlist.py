#!/usr/bin/env python3
"""Tests for Watchlist & Portfolio Monitoring Center."""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI

TEST_COMPANY = 5708

def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_watchlist_page_renders(client):
    res = client.get("/overvaagning")
    assert res.status_code == 200
    assert "Overvågning" in res.text or "Overvågede" in res.text

def test_watchlist_toggle_and_query_api(client):
    res = client.post("/api/watchlist/toggle", json={"canonical_entity_id": TEST_COMPANY, "entity_kind": "COMPANY"})
    assert res.status_code == 200
    assert "status" in res.json
    status = res.json["status"]
    assert status in ("added", "removed")

def test_company_page_contains_watchlist_button(client):
    res = client.get(f"/companies/{TEST_COMPANY}")
    assert res.status_code == 200
    assert "btn-watchlist" in res.text or "Overvåg" in res.text or "Følg" in res.text
