"""API consistency: /api/groups/<id>/flow must return JSON 404, not HTML.

Other API endpoints (/api/network/<id>, /api/inspect/<id>) return
{"error": "..."} JSON on 404. /api/groups/<id>/flow used abort(404) which
renders the HTML notfound template. API clients cannot parse that."""
import json
import pytest
from heimdal.app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


NONEXISTENT_ID = 999999


def test_groups_flow_404_returns_json(client):
    res = client.get(f"/api/groups/{NONEXISTENT_ID}/flow")
    assert res.status_code == 404
    # Content-Type must be JSON, not HTML
    assert "application/json" in res.content_type, (
        f"Expected JSON content-type, got: {res.content_type}"
    )
    data = res.json
    assert "error" in data
    assert data["error"] == "Entity not found"


def test_groups_flow_404_body_is_parseable_json(client):
    """The response body must be valid JSON — not the HTML notfound template."""
    res = client.get(f"/api/groups/{NONEXISTENT_ID}/flow")
    assert res.status_code == 404
    # Must not contain HTML doctype (which is what abort(404) renders)
    assert b"<!doctype html>" not in res.data.lower()
    # Must be parseable as JSON
    data = json.loads(res.data)
    assert isinstance(data, dict)
