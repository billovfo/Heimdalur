"""Verify UBO Simulator has been removed.
The simulator's logic was broken (added owners without diluting existing
ones, producing totals >100%). The module was removed pending a correct
rewrite. These tests guard against accidental re-introduction."""
import pytest
from heimdal.app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_simulator_route_returns_404(client):
    res = client.get("/simulator")
    assert res.status_code == 404, (
        f"/simulator still returns {res.status_code}, expected 404 after removal"
    )


def test_simulate_ubo_api_returns_404(client):
    res = client.post("/api/simulate/ubo", json={"company_id": 5708, "mutations": []})
    assert res.status_code == 404, (
        f"/api/simulate/ubo still returns {res.status_code}, expected 404 after removal"
    )


def test_simulator_not_in_nav(client):
    res = client.get("/")
    assert res.status_code == 200
    # The nav must not link to /simulator
    assert '"/simulator"' not in res.text, (
        "Nav still links to /simulator — UBO Simulator should be removed"
    )
    assert ">UBO Simulator<" not in res.text, (
        "Nav still shows 'UBO Simulator' label"
    )


def test_simulator_module_import_fails():
    """Importing the simulator service should fail after removal."""
    try:
        from heimdal.modules.simulator.service import simulate_ubo_cascades  # noqa: F401
        assert False, "simulator.service should not be importable after removal"
    except ModuleNotFoundError:
        pass
