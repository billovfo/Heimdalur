#!/usr/bin/env python3
"""Tests for Corporate Group Sankey Flow Visualizer."""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI

TEST_PARENT = 5653

def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_api_group_flow_endpoint(client):
    res = client.get(f"/api/groups/{TEST_PARENT}/flow")
    assert res.status_code == 200
    data = res.json
    assert "nodes" in data
    assert "links" in data
    assert isinstance(data["nodes"], list)
    assert isinstance(data["links"], list)
    assert len(data["nodes"]) > 0

def test_group_detail_page_contains_sankey_canvas(client):
    res = client.get(f"/groups/{TEST_PARENT}")
    assert res.status_code == 200
    assert "sankey-flow-container" in res.text
    assert "sankey_flow.js" in res.text
