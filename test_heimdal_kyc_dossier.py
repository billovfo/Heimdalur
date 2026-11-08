#!/usr/bin/env python3
"""Tests for Bank-KYC & Due Diligence Dossier Suite."""
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

def test_company_kyc_dossier_route(client):
    res = client.get(f"/companies/{TEST_COMPANY}/kyc")
    assert res.status_code == 200
    assert "KEND DIN KUNDE" in res.text or "KYC DUE DILIGENCE" in res.text
    assert "Skráseting Føroya" in res.text
    assert "observeret ejerandel" in res.text  # UBO wording retired (test_ubo_retired)
    assert "Registrerede strukturforhold" in res.text
    assert "Underskrift & Godkendelse" in res.text or "Godkendelse" in res.text

def test_company_page_has_kyc_button(client):
    res = client.get(f"/companies/{TEST_COMPANY}")
    assert res.status_code == 200
    assert f"/companies/{TEST_COMPANY}/kyc" in res.text
