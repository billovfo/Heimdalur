#!/usr/bin/env python3
"""Tests for Network Topology Graph API & Visualizer Engine."""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI

TEST_COMPANY = 5708
TEST_PERSON = 5761

def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_api_network_company(client):
    res = client.get(f"/api/network/{TEST_COMPANY}")
    assert res.status_code == 200
    data = res.json
    assert "center_node" in data
    assert "nodes" in data
    assert "edges" in data
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)
    assert data["center_node"]["id"] == TEST_COMPANY

def test_api_network_person(client):
    res = client.get(f"/api/network/{TEST_PERSON}")
    assert res.status_code == 200
    data = res.json
    assert "center_node" in data
    assert "nodes" in data
    assert "edges" in data
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)
    assert data["center_node"]["id"] == TEST_PERSON

def test_api_network_filter_threshold(client):
    res = client.get(f"/api/network/{TEST_COMPANY}?min_pct=50.0")
    assert res.status_code == 200
    data = res.json
    for edge in data["edges"]:
        if edge.get("parsed_percentage") is not None:
            assert edge["parsed_percentage"] >= 50.0

def test_company_page_contains_network_canvas_container(client):
    # PUX-21: the interactive graph lives on /network, the detail page has a preview
    res = client.get(f"/companies/{TEST_COMPANY}/network")
    assert res.status_code == 200
    assert "network-graph-container" in res.text
    assert "graph_network.js" in res.text

def test_person_page_contains_network_canvas_container(client):
    res = client.get(f"/persons/{TEST_PERSON}/network")
    assert res.status_code == 200
    assert "network-graph-container" in res.text
    assert "graph_network.js" in res.text

def test_graph_controls_rendered_in_html(client):
    res = client.get(f"/companies/{TEST_COMPANY}/network")
    assert res.status_code == 200
    assert "Tærskel" in res.text or "graph-threshold-slider" in res.text
    assert "Eksportér PNG" in res.text or "btn-export-graph" in res.text

