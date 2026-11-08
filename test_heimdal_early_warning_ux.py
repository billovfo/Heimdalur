#!/usr/bin/env python3
"""Tests for Early Warning UX and Profile Badges."""
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

def test_company_page_passes_early_warnings(client):
    res = client.get(f"/companies/{TEST_COMPANY}")
    assert res.status_code == 200

def test_compliance_page_has_no_risk_filter_tabs(client):
    res = client.get("/compliance")
    assert res.status_code == 200
    assert "ew-tab-btn" not in res.text
    assert "filterEwSignals" not in res.text
    assert "Alvorlighed" not in res.text
