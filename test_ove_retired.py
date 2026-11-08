"""O.V.E. is incomplete source metadata, not a compliance finding.

Skráseting Føroya supplies explicit O.V.E. status for only part of the
companies with no control registration. Heimdal must not turn that partial
source field into an AML or compliance conclusion. The old AML compliance
views are removed; only the neutral observation surface remains.
"""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI
from heimdal.modules.compliance.service import entity_structure_status


OVE_ONLY_COMPANY = 68


def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_ove_view_is_removed():
    with conn() as c:
        row = c.execute(
            "SELECT to_regclass('public.v_aml_ove_conflict') AS view_name"
        ).fetchone()
        assert row["view_name"] is None


def test_legacy_compliance_overview_view_is_removed():
    with conn() as c:
        row = c.execute(
            "SELECT to_regclass('public.v_aml_compliance_overview') AS view_name"
        ).fetchone()
        assert row["view_name"] is None


def test_ove_only_company_has_no_structure_observations():
    with conn() as c:
        status = entity_structure_status(c, OVE_ONLY_COMPANY)
        assert status["has_observations"] is False
        assert status["observation_count"] == 0
        assert status["observations"] == []


def test_compliance_page_has_no_ove_copy_or_filter(client):
    res = client.get("/compliance")
    assert res.status_code == 200
    assert "O.V.E.-konflikter" not in res.text
    assert "O.V.E. Sandhedstjek" not in res.text
    assert "formelt registrerede kontrolaktører" not in res.text


def test_home_and_kyc_pages_do_not_claim_ove_detection(client):
    home = client.get("/")
    kyc = client.get("/companies/5708/kyc")
    assert home.status_code == 200
    assert kyc.status_code == 200
    assert "O.V.E.-konflikter" not in home.text
    assert "O.V.E.-konflikter" not in kyc.text
