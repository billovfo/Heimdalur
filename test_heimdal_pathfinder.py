#!/usr/bin/env python3
"""Tests for Entity Pathfinder (Shortest Path & Graph Traversal)."""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI
from heimdal.modules.graph.repository import GraphRepository
from heimdal.modules.graph.service import find_entity_paths

TEST_COMPANY_WITH_ROLES = 5708
TEST_PERSON_FULL = 5761
TEST_PERSON_NO_RELATIONS = 6

def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_pathfinder_finds_direct_relation():
    with conn() as c:
        # Test person owning company / related to company
        res = find_entity_paths(GraphRepository(c), from_canonical_id=TEST_PERSON_FULL, to_canonical_id=TEST_COMPANY_WITH_ROLES, max_depth=3)
        assert "paths" in res
        assert "length" in res
        assert isinstance(res["paths"], list)

def test_pathfinder_returns_empty_on_disconnected():
    with conn() as c:
        # an isolated person
        res = find_entity_paths(GraphRepository(c), from_canonical_id=TEST_PERSON_NO_RELATIONS, to_canonical_id=TEST_COMPANY_WITH_ROLES, max_depth=2)
        assert res["found"] is False
        assert len(res["paths"]) == 0

def test_pathfinder_same_entity():
    with conn() as c:
        res = find_entity_paths(GraphRepository(c), from_canonical_id=TEST_COMPANY_WITH_ROLES, to_canonical_id=TEST_COMPANY_WITH_ROLES)
        assert res["found"] is True
        assert res["length"] == 0

def test_pathfinder_page_renders(client):
    res = client.get("/find-forbindelse")
    assert res.status_code == 200
    assert "Find forbindelse</h1>" in res.text  # was the dev label "Graf-Stifinder"
    assert "Find dokumenterede forbindelser" in res.text

def test_pathfinder_api_endpoint(client):
    res = client.get(f"/api/pathfinder?from={TEST_PERSON_FULL}&to={TEST_COMPANY_WITH_ROLES}")
    assert res.status_code == 200
    data = res.json
    assert "found" in data
    assert "paths" in data

def test_pathfinder_weighted_ranking_and_hubs():
    with conn() as c:
        res = find_entity_paths(GraphRepository(c), from_canonical_id=TEST_PERSON_FULL, to_canonical_id=TEST_COMPANY_WITH_ROLES)
        assert "intermediary_hubs" in res
        assert "summary_explanation" in res
        assert isinstance(res["intermediary_hubs"], list)

def test_pathfinder_ui_renders_hubs_and_weights(client):
    res = client.get(f"/find-forbindelse?from={TEST_PERSON_FULL}&to={TEST_COMPANY_WITH_ROLES}")
    assert res.status_code == 200
    assert "Find forbindelse</h1>" in res.text


def test_pathfinder_api_missing_params_returns_400_with_clear_message(client):
    """When from/to are absent, the error must name the missing params."""
    res = client.get("/api/pathfinder")
    assert res.status_code == 400
    data = res.json
    assert "error" in data
    assert "required" in data["error"].lower()


def test_pathfinder_api_non_integer_params_returns_400_with_type_message(client):
    """When from/to are present but not integers, the error must say so —
    not claim the params are missing."""
    res = client.get("/api/pathfinder?from=abc&to=def")
    assert res.status_code == 400
    data = res.json
    assert "error" in data
    msg = data["error"].lower()
    # The message must NOT say 'required' (that's the missing-param message)
    assert "required" not in msg, (
        f"Non-int params should get a type error, got: {data['error']!r}"
    )
    # The message must mention integers or 'numeric' or 'invalid'
    assert any(w in msg for w in ("integer", "numeric", "invalid", "number")), (
        f"Expected type-error wording, got: {data['error']!r}"
    )


def test_pathfinder_api_one_missing_param_returns_400(client):
    """Only one of from/to present must still 400 with missing wording."""
    res = client.get(f"/api/pathfinder?from={TEST_PERSON_FULL}")
    assert res.status_code == 400
    data = res.json
    assert "required" in data["error"].lower()



