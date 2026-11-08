#!/usr/bin/env python3
"""Tests for Batch-Screening & Bulk Audit Suite."""
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

def test_bulk_screening_page_renders(client):
    res = client.get("/bulk-screening")
    assert res.status_code == 200
    assert "Bulk Screening" in res.text

def test_bulk_screening_hero_is_factual(client):
    res = client.get("/bulk-screening")
    assert res.status_code == 200
    assert "UBO-mangler" not in res.text
    assert "AML-risiko" not in res.text
    assert "PEP" in res.text
    assert "struktur-observationer" in res.text

def test_api_bulk_screen_endpoint(client):
    payload = {
        "identifiers": ["3690", "5708", "Testa Testersen"]
    }
    res = client.post("/api/bulk/screen", json=payload)
    assert res.status_code == 200
    data = res.json
    assert "results" in data
    assert len(data["results"]) >= 2
    assert "observation_count" in data["results"][0]

def test_bulk_results_are_factual_not_scored(client):
    res = client.post("/api/bulk/screen", json={"identifiers": ["5708"]})
    row = res.json["results"][0]
    assert "observation_count" in row
    assert "aml_risk_score" not in row
    assert "risk_level" not in row
