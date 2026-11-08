#!/usr/bin/env python3
"""Tests for Early Warning & Pattern Detection."""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI
from heimdal.modules.compliance.repository import registration_patterns

def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_registration_patterns_view_exists():
    with conn() as c:
        assert isinstance(c.execute("SELECT * FROM v_registration_patterns LIMIT 5").fetchall(), list)

def test_registration_patterns_helper():
    with conn() as c:
        assert isinstance(registration_patterns(c, 5708), list)

def test_observation_page_does_not_render_early_warning_copy(client):
    res = client.get("/compliance")
    assert res.status_code == 200
    assert "Registreringsmønstre" in res.text
    assert "Early Warning" not in res.text
