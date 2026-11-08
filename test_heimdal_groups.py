#!/usr/bin/env python3
"""Tests for Corporate Group & Ultimate Parent Detection."""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI
from heimdal.modules.groups.service import (
    all_corporate_groups, calculate_group_control_metrics, corporate_group_tree,
    ultimate_parent_of,
)

TEST_COMPANY_WITH_ROLES = 5708

def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_ultimate_parent_resolution():
    with conn() as c:
        group_info = ultimate_parent_of(c, TEST_COMPANY_WITH_ROLES)
        assert group_info is not None
        assert "is_in_group" in group_info
        assert "ultimate_parent" in group_info

def test_corporate_groups_list():
    with conn() as c:
        groups = all_corporate_groups(c, limit=10)
        assert isinstance(groups, list)
        if groups:
            g = groups[0]
            assert "parent_id" in g
            assert "parent_name" in g
            assert "subsidiary_count" in g

def test_corporate_group_tree():
    with conn() as c:
        groups = all_corporate_groups(c, limit=1)
        if groups:
            tree = corporate_group_tree(c, groups[0]["parent_id"])
            assert tree is not None
            assert "parent" in tree
            assert "tree" in tree

def test_groups_page_renders(client):
    res = client.get("/groups")
    assert res.status_code == 200
    assert "<h1>Koncerner</h1>" in res.text
    assert "Magtgruppe" not in res.text  # dev label removed (frontend quality §110)

def test_company_page_displays_group_box(client):
    res = client.get(f"/companies/{TEST_COMPANY_WITH_ROLES}")
    assert res.status_code == 200
    assert "Koncern" in res.text

def test_group_cash_flow_vs_control_calculation():
    with conn() as c:
        groups = all_corporate_groups(c, limit=1)
        if groups:
            pid = groups[0]["parent_id"]
            metrics = calculate_group_control_metrics(c, pid)
            assert "subsidiaries" in metrics
            assert "total_subsidiaries" in metrics
            if metrics["subsidiaries"]:
                s0 = metrics["subsidiaries"][0]
                assert "control_pct" in s0
                assert "cash_flow_pct" in s0

def test_group_detail_displays_cash_flow_metrics(client):
    with conn() as c:
        groups = all_corporate_groups(c, limit=1)
        if groups:
            pid = groups[0]["parent_id"]
            res = client.get(f"/groups/{pid}")
            assert res.status_code == 200
            assert "Effektiv kapitalandel" in res.text or "Kapitalandel" in res.text


def test_ultimate_parent_is_never_person():
    """Regression: ultimate_parent_of must never return a PERSON as the
    ultimate parent. A person is not a corporate parent."""
    with conn() as c:
        # Test several companies known to be person-owned
        for company_id in (7, 5115, 4276, 1627, 5634):
            info = ultimate_parent_of(c, company_id)
            assert info["ultimate_parent"]["entity_kind"] != "PERSON", (
                f"Company {company_id}: ultimate_parent is PERSON "
                f"({info['ultimate_parent']['display_name']})"
            )


def test_groups_list_has_no_person_parents():
    """Regression: the /groups page must never list a PERSON as a parent."""
    with conn() as c:
        groups = all_corporate_groups(c, limit=100)
        for g in groups:
            assert g["parent_kind"] != "PERSON", (
                f"/groups list contains PERSON parent: {g['parent_name']}"
            )


