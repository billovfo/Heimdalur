#!/usr/bin/env python3
"""Tests for Data Export & Due Diligence Dossier."""
import csv
import io
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI

TEST_COMPANY_WITH_ROLES = 5708
TEST_PERSON_FULL = 5761

def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_company_owners_csv_export(client):
    res = client.get(f"/api/export/company/{TEST_COMPANY_WITH_ROLES}/owners.csv")
    assert res.status_code == 200
    assert res.headers["Content-Type"].startswith("text/csv")
    reader = csv.reader(io.StringIO(res.text))
    header = next(reader)
    assert "owner_name" in header

def test_company_ejarayvirlit_csv_export(client):
    res = client.get(f"/api/export/company/{TEST_COMPANY_WITH_ROLES}/ejarayvirlit.csv")
    assert res.status_code == 200
    assert res.headers["Content-Type"].startswith("text/csv")
    reader = csv.reader(io.StringIO(res.text))
    header = next(reader)
    assert "person_name" in header

def test_compliance_matrix_csv_export(client):
    res = client.get("/api/export/compliance/matrix.csv")
    assert res.status_code == 200
    assert res.headers["Content-Type"].startswith("text/csv")
    reader = csv.reader(io.StringIO(res.text))
    header = next(reader)
    assert "company_name" in header

def test_compliance_matrix_json_export(client):
    res = client.get("/api/export/compliance/matrix.json")
    assert res.status_code == 200
    assert res.headers["Content-Type"].startswith("application/json")
    data = res.json
    assert isinstance(data, list)

def test_company_dossier_renders(client):
    res = client.get(f"/companies/{TEST_COMPANY_WITH_ROLES}/dossier")
    assert res.status_code == 200
    assert "Due Diligence Dossier" in res.text
    assert "Executive Summary" in res.text
    assert "Proveniens & Kildedokumentation" in res.text or "Proveniens" in res.text

def test_person_dossier_renders(client):
    res = client.get(f"/persons/{TEST_PERSON_FULL}/dossier")
    assert res.status_code == 200
    assert "Due Diligence Dossier" in res.text

def test_dossier_contains_print_header_and_disclaimer(client):
    res = client.get(f"/companies/{TEST_COMPANY_WITH_ROLES}/dossier")
    assert res.status_code == 200
    assert "Kildeforbehold (Skráseting Føroya)" in res.text
    assert "under opbygning" in res.text or "ikke er færdigudviklet" in res.text

def test_kyc_dossier_does_not_render_a_risk_score(client):
    res = client.get("/companies/5708/kyc")
    assert res.status_code == 200
    for forbidden in ("AML RISIKO INDEKS", "/ 100", "Risikoklasse", "Skærpet Due Diligence", "Standard Kundekendskab"):
        assert forbidden not in res.text
    assert "Registrerede strukturforhold" in res.text

