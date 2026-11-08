"""Network boundary (audit 2026-11 I-6, F-7, E-6). Offline: needs no DSN and
no DB. Every request here is refused or answered before any DB access."""
import pytest

from heimdal.app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_foreign_host_is_400(client):
    assert client.get("/favicon.ico", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/favicon.ico", headers={"Host": "rebind.example:8408"}).status_code == 400


def test_loopback_get_passes(client):
    assert client.get("/favicon.ico").status_code == 200
    assert client.get("/favicon.ico", headers={"Host": "127.0.0.1:8408"}).status_code == 200
    assert client.get("/favicon.ico", environ_base={"REMOTE_ADDR": "::1"}).status_code == 200


def test_non_loopback_client_is_403(client):
    assert client.get("/favicon.ico", environ_base={"REMOTE_ADDR": "10.0.0.5"}).status_code == 403


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "null"},  # opaque origin: sandboxed iframe, no-referrer page
    {"Origin": "http://localhost.evil.example"},
    {"Referer": "http://evil.example/page"},
])
def test_foreign_post_is_403(client, headers):
    r = client.post("/api/watchlist/toggle", json={}, headers=headers)
    assert r.status_code == 403
    r = client.post("/persons/review/1/decision", data={"decision": "REJECT"}, headers=headers)
    assert r.status_code == 403


@pytest.mark.parametrize("headers", [
    {},  # curl / test client
    {"Origin": "http://localhost"},
    {"Origin": "http://127.0.0.1:8408"},
    {"Referer": "http://127.0.0.1:8408/overvaagning"},
])
def test_same_origin_post_passes_guard(client, headers):
    # Empty body: the route answers 400 before it opens a DB connection.
    assert client.post("/api/watchlist/toggle", json={}, headers=headers).status_code == 400


def test_review_freeze_requires_json(client, monkeypatch):
    monkeypatch.setenv("HEIMDAL_REVIEW_UI", "1")
    # Dead port: if the JSON check is ever lost, this fails loudly, never writes.
    monkeypatch.setenv("HEIMDAL_PDFHIST_PROJECTION_DSN", "postgresql://audit@127.0.0.1:1/none")
    assert client.post("/persons/review/batch/freeze").status_code == 415
