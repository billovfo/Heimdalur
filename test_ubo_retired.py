"""UBO_UNREGISTERED is absence-based, not a compliance finding.

Control-registration coverage is incomplete: Skráseting Føroya does not
consistently record who is a registered control actor. So "owns >=25% but
missing from v_control_registration" is the same source defect as the retired
O.V.E. signal — it cannot support an AML or compliance conclusion.
"""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI
from heimdal.modules.compliance.repository import structure_observation_summary


def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_ubo_view_is_removed():
    with conn() as c:
        row = c.execute(
            "SELECT to_regclass('public.v_aml_ubo_unregistered') AS view_name"
        ).fetchone()
        assert row["view_name"] is None


def test_compliance_overview_has_no_ubo_rows():
    with conn() as c:
        row = c.execute("""
            SELECT count(*) AS n
            FROM v_structure_observations
            WHERE observation_code IN ('UBO_UNREGISTERED', 'OVE_CONFLICT')
        """).fetchone()
        assert row["n"] == 0


def test_compliance_summary_uses_neutral_keys():
    with conn() as c:
        summary = structure_observation_summary(c)
        assert "ubo_unregistered_count" not in summary
        assert "ownership_cycle_count" in summary


def test_compliance_page_has_no_ubo_text_or_filter(client):
    res = client.get("/compliance")
    assert res.status_code == 200
    assert "UBO Flagging" not in res.text
    assert "UBO_UNREGISTERED" not in res.text
    assert "&gt;25% uregistreret" not in res.text


def test_company_page_does_not_signal_ubo(client):
    res = client.get("/companies/5708")
    assert res.status_code == 200
    assert "UBO" not in res.text


def test_home_page_does_not_claim_ubo_control(client):
    home = client.get("/")
    assert home.status_code == 200
    assert "uregistrerede reelle ejere" not in home.text
    assert "AML / UBO Flag" not in home.text
