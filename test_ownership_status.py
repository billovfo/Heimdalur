"""Direct ownership status is factual register data, not an AML score."""
from decimal import Decimal
import pytest
from heimdal.app import app
from heimdal.modules.entities.service import ownership_sum


def test_ownership_sum_exposes_largest_parsed_share():
    rows = [
        {"parsed_percentage": Decimal("20"), "percentage_parse_status": "PARSED"},
        {"parsed_percentage": Decimal("55"), "percentage_parse_status": "PARSED"},
        {"parsed_percentage": Decimal("25"), "percentage_parse_status": "PARSED"},
    ]
    result = ownership_sum(rows)
    assert result["largest"] == Decimal("55")
    assert result["total"] == Decimal("100")


def test_ownership_sum_largest_is_none_when_no_share_can_be_parsed():
    rows = [
        {"parsed_percentage": None, "percentage_parse_status": "FAILED"},
    ]
    result = ownership_sum(rows)
    assert result["largest"] is None
    assert result["unreadable"] == 1


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_company_page_shows_largest_registered_direct_share(client):
    # BS Tøkni (7) has one direct owner at 100% in the current snapshot.
    res = client.get("/companies/7")
    assert res.status_code == 200
    assert "Største registrerede direkte ejerandel" in res.text
    assert "100" in res.text


def test_company_page_does_not_call_largest_share_a_risk_score(client):
    res = client.get("/companies/7")
    assert res.status_code == 200
    assert "Ejerkoncentrationsscore" not in res.text
    assert "AML-risikoscore" not in res.text


def test_dossier_does_not_render_a_risk_verdict(client):
    res = client.get("/companies/7/dossier")
    assert res.status_code == 200
    assert "Registrerede strukturforhold" in res.text
    assert "AML / Compliance Status" not in res.text
    assert "VERIFICERET OK" not in res.text
    assert "FLAGGET" not in res.text
