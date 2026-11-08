"""Neutral structure-observation views.

The only ownership observations are a closed ownership cycle and multiple
registered people with the same surname whose direct shares sum to 25% or
more. The only registration-pattern observations are five or more registered
company roles and direct registered ownership shares whose parsed sum exceeds
100%. Both are observations of the local snapshot, not allegations.
"""
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


def test_neutral_observation_views_replace_risk_views():
    with conn() as c:
        assert c.execute("SELECT to_regclass('v_structure_observations') AS name").fetchone()["name"]
        assert c.execute("SELECT to_regclass('v_registration_patterns') AS name").fetchone()["name"]


def test_structure_observation_types_are_neutral():
    with conn() as c:
        types = {row["observation_type"] for row in c.execute(
            "SELECT DISTINCT observation_type FROM v_structure_observations"
        ).fetchall()}
    assert types == {"OWNERSHIP_CYCLE", "SAME_SURNAME_SHARE"}


def test_public_pages_do_not_reintroduce_risk_or_verdict_copy(client):
    for path in ("/", "/compliance", "/companies/5708", "/companies/5708/dossier", "/companies/5708/kyc", "/bulk-screening"):
        res = client.get(path)
        assert res.status_code == 200
        for forbidden in (
            "AML / Hvidvask Compliance Matrix", "AML RISIKO INDEKS", "Risikoklasse",
            "Uoverensstemmelse", "VERIFICERET OK", "HØJ RISIKO", "Early Warning",
            "UBO_UNREGISTERED", "OVE_CONFLICT",
        ):
            assert forbidden not in res.text, (path, forbidden)
