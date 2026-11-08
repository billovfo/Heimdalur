#!/usr/bin/env python3
"""Tests for Bulk Screening KPI bar and UI polish."""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_bulk_page_contains_kpi_bar_and_filters(client):
    res = client.get("/bulk-screening")
    assert res.status_code == 200
    assert "bulk-kpi-bar" in res.text or "kpi-box" in res.text
    assert "filterBulkResults" in res.text or "filter-chip" in res.text
    assert 'data-action="print"' in res.text  # print via app.js, no inline handler (G-05)
    assert 'src="/static/bulk_screening.js"' in res.text
