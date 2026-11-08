"""Audit F-5, F-6, F-10, F-12, F-14 regressions, offline: every malformed input
is a 4xx decided before any DB access, and the DB-facing parts run on fakes."""
import contextlib
import inspect

import pytest

from heimdal.app import create_app
from heimdal.modules.shared import web
from heimdal.modules.shared.inputs import clamp_offset
from heimdal.modules.screening.service import batch_screen_entities


@pytest.fixture
def app():
    a = create_app()
    a.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return a


@pytest.fixture
def client(app):
    return app.test_client()


class FakeConn:
    """Records (sql, params); `answer(sql, params)` returns the rows."""
    def __init__(self, answer=lambda sql, params: []):
        self.sql, self.answer = [], answer

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        rows = self.answer(sql, params)
        return type("Cur", (), {"fetchall": lambda s: rows,
                                "fetchone": lambda s: rows[0] if rows else None})()

    def commit(self):
        self.sql.append(("COMMIT", None))


# --- F-6 ---------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "/api/search?q=a%00b", "/companies?q=%00", "/persons?q=%00", "/groups?q=%00",
    "/compliance?q=%00", "/aendringer?category=%00",
    "/aendringer?from_date=abc", "/aendringer?to_date=2026-13-45", "/aendringer?from_date=2026-W45-4x",
])
def test_bad_query_args_are_400_before_any_db_access(client, url):
    assert client.get(url).status_code == 400


def test_clamp_offset_is_the_entities_clamp():
    assert clamp_offset((10 ** 20 - 1) * 50, 120, 50) == 100  # ?page=1e20 -> last page
    assert clamp_offset(100, 120, 50) == 100
    assert clamp_offset(0, 0, 50) == 0


# --- F-5 ---------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    [], ["5708"], "abc", 5, {"identifiers": 5}, {"identifiers": "abc"}, {"identifiers": [1]},
    {"identifiers": [None]}, {"identifiers": ["a\x00b"]}, {"identifiers": ["x"] * 201},
])
def test_bulk_screen_rejects_bad_shapes(client, body):
    assert client.post("/api/bulk/screen", json=body).status_code == 400


def test_bulk_screen_is_batched_and_a_number_is_a_registration_number():
    def answer(sql, params):
        if "registration_number = ANY" in sql:
            return [{"canonical_entity_id": 1507, "display_name": "S267 Co", "entity_kind": "COMPANY",
                     "registration_number": "S267", "key": "S267"}]
        if "translate(lower(display_name)" in sql:  # two homonyms
            return [{"canonical_entity_id": i, "display_name": "Jógvan Testsen", "entity_kind": "PERSON",
                     "registration_number": None, "key": "jogvan testsen"} for i in (5, 6)]
        if "v_structure_observations" in sql:
            return [{"company_id": 1507, "n": 2}]
        return []
    c = FakeConn(answer)
    ids = ["267", "Jógvan Testsen", "kilde:17681", " "] + [f"S{n}" for n in range(196)]
    rows = {r["query"]: r for r in batch_screen_entities(c, ids)}
    assert len(c.sql) <= 5  # one query per identifier kind + PEP + observations, not 2 per item
    assert rows["267"]["id"] == 1507 and rows["267"]["regnr"] == "S267"  # never canonical 267
    assert rows["267"]["observation_count"] == 2
    assert rows["Jógvan Testsen"]["id"] is None and rows["Jógvan Testsen"]["match_count"] == 2
    assert rows["kilde:17681"]["id"] is None and rows["kilde:17681"]["match_count"] == 0
    assert " " not in rows and len(rows) == 199
    regnr_sql = next(p for s, p in c.sql if "registration_number = ANY" in s)
    assert "S267" in regnr_sql[0] and all(isinstance(x, str) for x in regnr_sql[0])


# --- F-10 --------------------------------------------------------------------

def test_no_fake_historical_endpoints_and_no_depth(app):
    from heimdal.modules.graph.service import get_entity_network_graph
    assert not [r.rule for r in app.url_map.iter_rules() if "historical" in r.rule]
    assert "depth" not in inspect.signature(get_entity_network_graph).parameters


# --- F-12 --------------------------------------------------------------------

def test_rate_limiter_evicts_expired_ips(app, monkeypatch):
    app.config["TESTING"] = False  # _rate_limit is a no-op under TESTING
    monkeypatch.setattr(web, "_requests", {})
    monkeypatch.setattr(web, "_last_sweep", [0.0])
    clock = [1000.0]
    monkeypatch.setattr(web.time, "monotonic", lambda: clock[0])
    window = app.config["RATE_WINDOW"]

    def hit(ip):
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": ip}):
            assert web._rate_limit() is None

    for i in range(1000):
        hit(f"127.0.{i // 256}.{i % 256}")
    assert len(web._requests) == 1000
    clock[0] += window + 1
    hit("127.1.0.1")
    assert list(web._requests) == ["127.1.0.1"]


# --- F-14 --------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    [1], "abc", {"canonical_entity_id": "abc"}, {"canonical_entity_id": "1"},
    {"canonical_entity_id": 1.5}, {"canonical_entity_id": True}, {"canonical_entity_id": 1e20},
    {"canonical_entity_id": None}, {},
])
def test_watchlist_toggle_rejects_non_int_ids(client, body):
    assert client.post("/api/watchlist/toggle", json=body).status_code == 400


def test_watchlist_toggle_kind_from_canonical_entity_and_unknown_id_writes_nothing(client, monkeypatch):
    from heimdal.modules.watchlist import routes, service
    kinds = {1: "PERSON"}
    c = FakeConn(lambda sql, p: ([{"entity_kind": kinds[p[0]]}] if p[0] in kinds else [])
                 if sql.startswith("SELECT entity_kind") else [])
    w = FakeConn()  # B-9: the write transaction, never the read connection
    monkeypatch.setattr(routes, "conn", lambda: c)
    monkeypatch.setattr(service, "tx", lambda: contextlib.nullcontext(w))
    r = client.post("/api/watchlist/toggle", json={"canonical_entity_id": 999999999})
    assert r.status_code == 404 and len(c.sql) == 1 and not w.sql  # one read, no write
    r = client.post("/api/watchlist/toggle",
                    json={"canonical_entity_id": 1, "entity_kind": "COMPANY"})
    assert r.status_code == 200 and r.get_json()["status"] == "added"
    insert = next(p for s, p in w.sql if s.startswith("INSERT"))
    assert insert[:2] == (1, "PERSON")  # the client's "COMPANY" is ignored
    assert any(s.startswith("DELETE") and "RETURNING" in s for s, _ in w.sql)
    assert all(s.startswith("SELECT") for s, _ in c.sql)
