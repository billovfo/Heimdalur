"""Verify heimdal package skeleton and config constants."""
from decimal import Decimal
from flask import Flask
from heimdal.config import (
    DB_URI, HUNDRED, INCOMING, OUTGOING, _DIRECT_COLS,
    PAGE_SIZE, PERSON_OWNERS_CAP, NETWORK_NODE_CAP,
    FETCHED_OK, FETCH_FAILED, NEVER_FETCHED,
    _RATE_LIMIT, _RATE_WINDOW, _RATE_EXCEEDED_TEXT,
)


def test_config_constants_exist():
    assert DB_URI.startswith("postgresql://")
    assert HUNDRED == Decimal("100")
    assert INCOMING == "INCOMING"
    assert OUTGOING == "OUTGOING"
    assert "relation_id" in _DIRECT_COLS
    assert PAGE_SIZE == 60
    assert PERSON_OWNERS_CAP == 100
    assert NETWORK_NODE_CAP == 60
    assert FETCHED_OK == "FETCHED_OK"
    assert FETCH_FAILED == "FETCH_FAILED"
    assert NEVER_FETCHED == "NEVER_FETCHED"
    assert _RATE_LIMIT == 300
    assert _RATE_WINDOW == 60
    assert "forespørgsler" in _RATE_EXCEEDED_TEXT


from heimdal.modules.shared.db import conn, _close


def test_conn_function_exists():
    assert callable(conn)
    assert callable(_close)


from heimdal.modules.shared.web import (
    _requests, _requests_lock, _rate_limit, _security_headers,
)


def test_web_helpers_exist():
    assert isinstance(_requests, dict)
    assert hasattr(_requests_lock, "acquire")
    assert callable(_rate_limit)
    assert callable(_security_headers)


from heimdal.modules.shared.entity import (
    entity, provenance, fetch_state, entity_url, entity_relations,
)
from heimdal.config import NEVER_FETCHED, FETCHED_OK, FETCH_FAILED


def test_entity_helpers_exist():
    assert callable(entity)
    assert callable(provenance)
    assert callable(fetch_state)
    assert callable(entity_url)
    assert callable(entity_relations)


def test_fetch_state_none():
    assert fetch_state(None) == NEVER_FETCHED


def test_fetch_state_ok():
    class FakeProv:
        last_error_category = "OK"
        def __getitem__(self, key):
            return getattr(self, key)
    assert fetch_state(FakeProv()) == FETCHED_OK


def test_fetch_state_failed():
    class FakeProv:
        last_error_category = "HTTP_404"
        def __getitem__(self, key):
            return getattr(self, key)
    assert fetch_state(FakeProv()) == FETCH_FAILED


def test_entity_url_person():
    assert entity_url("PERSON", 42) == "/persons/42"


def test_entity_url_company():
    assert entity_url("COMPANY", 42) == "/companies/42"


def test_entity_url_fallback():
    assert entity_url("ORGANIZATION", 42) == "/entities/42"
    assert entity_url("PUBLIC_AUTHORITY", 42) == "/entities/42"
    assert entity_url("UNKNOWN_ENTITY", 42) == "/entities/42"


from heimdal.modules.shared.csv_export import csv_response


def test_csv_response_exists():
    assert callable(csv_response)


from decimal import Decimal
from heimdal.modules.shared.templates import pct, dk, heatband, fail


def test_pct_none():
    assert pct(None) is None


def test_pct_integer():
    # T6.4 (plan v1/F-31/F-32): fast to-decimaler dansk format — "50,00" er
    # den konsekvente kontrakt; variabel præcision var fire forskellige
    # formater på én flade. Intent (dansk komma, ingen float-noise) bevaret.
    assert pct(Decimal("50")) == "50,00"


def test_pct_decimal():
    assert pct(Decimal("33.3333")) == "33,33"


def test_pct_float_noise():
    # 7.826910000000001 → quantized to 2 decimals → "7,83"
    assert pct(7.826910000000001) == "7,83"


def test_dk_none():
    assert dk(None) == "—"


import datetime
def test_dk_value():
    assert dk(datetime.datetime(2026, 8, 16, 14, 30)) == "16-08-2026 14:30"


def test_heatband_zero():
    assert heatband(0) == "n-0"
    assert heatband(None) == "n-0"


def test_heatband_low():
    assert heatband(1) == "n-1"
    assert heatband(2) == "n-1"


def test_heatband_mid():
    assert heatband(3) == "n-2"
    assert heatband(5) == "n-2"


def test_heatband_high():
    assert heatband(6) == "n-3"
    assert heatband(100) == "n-3"


def test_fail_raises():
    import pytest
    with pytest.raises(AssertionError):
        fail("boom")


from heimdal.app import create_app, app


def test_app_factory():
    assert isinstance(app, Flask)
    assert callable(create_app)


def test_app_has_security_headers():
    # Note: factory app has no routes registered yet, so /favicon.ico returns
    # 404 — but after_request still runs, so security headers are present.
    a = create_app()
    with a.test_client() as client:
        res = client.get("/favicon.ico")
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "DENY"


def test_app_has_template_filters():
    a = create_app()
    assert "pct" in a.jinja_env.filters
    assert "dk" in a.jinja_env.filters
    assert "heatband" in a.jinja_env.filters


def test_app_has_template_globals():
    a = create_app()
    assert "fail" in a.jinja_env.globals
    assert "entity_url" in a.jinja_env.globals
