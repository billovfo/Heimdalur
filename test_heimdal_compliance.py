#!/usr/bin/env python3
"""Tests for AML & UBO Compliance Matrix."""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI
from heimdal.modules.compliance.repository import (
    structure_observation_matrix, structure_observation_summary,
)
from heimdal.modules.compliance.service import entity_structure_status

TEST_COMPANY_WITH_ROLES = 5708

def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_compliance_views_exist():
    with conn() as c:
        assert c.execute("SELECT to_regclass('v_structure_observations') AS name").fetchone()["name"]
        assert c.execute("SELECT to_regclass('v_registration_patterns') AS name").fetchone()["name"]

def test_overview_remaining_categories():
    with conn() as c:
        rows = c.execute(
            "SELECT DISTINCT observation_type FROM v_structure_observations"
        ).fetchall()
        types = {r["observation_type"] for r in rows}
        assert types == {"OWNERSHIP_CYCLE", "SAME_SURNAME_SHARE"}, (
            f"observation types must be cycle/surname only, got {types}"
        )
        cycle = c.execute(
            "SELECT count(*) AS n FROM v_structure_observations WHERE observation_type = 'OWNERSHIP_CYCLE'"
        ).fetchone()
        surname = c.execute(
            "SELECT count(*) AS n FROM v_structure_observations WHERE observation_type = 'SAME_SURNAME_SHARE'"
        ).fetchone()
        assert cycle["n"] > 0
        assert surname["n"] > 0

def test_compliance_summary_totals():
    with conn() as c:
        summary = structure_observation_summary(c)
        assert "total_observed_companies" in summary
        assert "ubo_unregistered_count" not in summary
        assert "ownership_cycle_count" in summary
        assert "ove_conflict_count" not in summary
        assert summary["total_observed_companies"] > 0

def test_entity_structure_status_keys():
    with conn() as c:
        res = entity_structure_status(c, TEST_COMPANY_WITH_ROLES)
        assert "has_observations" in res
        assert "observations" in res
        assert isinstance(res["observations"], list)
        assert "observation_count" in res

def test_compliance_matrix_pagination_and_search():
    with conn() as c:
        res = structure_observation_matrix(c, limit=10, offset=0)
        assert "total" in res
        assert "rows" in res
        assert len(res["rows"]) <= 10
        assert res["total"] > 0

def test_observation_route_uses_neutral_language(client):
    res = client.get("/compliance")
    assert res.status_code == 200
    assert "Registerobservationer" in res.text
    assert "Lukket ejerrelation" in res.text
    for forbidden in ("AML / Hvidvask", "Compliance Matrix", "Early Warning", "Alvorlighed", "Risiko", "uoverensstemmelse"):
        assert forbidden not in res.text

def test_company_profile_uses_neutral_observation_badges(client):
    # NAX (5708) has a registered OWNERSHIP_CYCLE observation and a
    # DIRECT_SHARE_SUM_OVER_100 pattern, so both neutral badges render.
    res = client.get(f"/companies/{TEST_COMPANY_WITH_ROLES}")
    assert res.status_code == 200
    # badge copy is sentence case since the 2026-09 redesign (CSS may uppercase it)
    assert ">Registerobservation (1)</a>" in res.text
    assert ">Registreringsmønster</a>" in res.text
    for forbidden in ("REGISTER-SIGNAL", "EW:", "badge-danger", "Ingen registersignaler"):
        assert forbidden not in res.text

def test_person_profile_shows_pattern_badge_only(client):
    # Person 8335 has a MANY_REGISTERED_ROLES pattern and no
    # ownership observation, so only the pattern badge renders.
    res = client.get("/persons/8335")
    assert res.status_code == 200
    assert ">Registreringsmønster</a>" in res.text
    assert ">Registerobservation (" not in res.text
    assert "EW:" not in res.text

def test_compliance_page_has_neutral_snapshot_disclaimer(client):
    res = client.get("/compliance")
    assert res.status_code == 200
    assert "kan være ufuldstændigt" in res.text
    assert "siger intet om lovlighed eller risiko" in res.text

def test_register_patterns_view_exists():
    with conn() as c:
        rows = c.execute("SELECT count(*) AS n FROM v_registration_patterns").fetchone()
        assert rows is not None
        assert "n" in rows



