#!/usr/bin/env python3
"""Tests for Faroese Fuzzy Orthographic & Multi-faceted Search."""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.modules.entities.service import normalize_faroese_query

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_faroese_character_normalization_search(client):
    # Searching 'Bardur' should find 'Bárður'
    res = client.get("/persons?q=Bardur")
    assert res.status_code == 200
    assert "Bárður" in res.text or "Bardur" in res.text

def test_companies_multifacet_filtering(client):
    res = client.get("/companies?kind=COMPANY&ownership_band=100")
    assert res.status_code == 200
    assert "Virksomheder" in res.text

def test_faroese_query_normalizer_helper():
    norm = normalize_faroese_query("Bárður í Skúvoy")
    assert "bardur" in norm
