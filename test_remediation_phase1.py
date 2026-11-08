#!/usr/bin/env python3
"""Phase 1 (T1.1–T1.6) detector suite — remediation plan v1.

Run:  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
      HEIMDAL_READ_DB=$HEIMDAL_READ_DB \
      python -m pytest test_remediation_phase1.py -q

Covers:
  T1.1/T1.2 bulk page: current naming, no stale label, kind-correct API contract.
  T1.3 watchlist: no delegated double-handler, single invocation path.
  T1.4 dashboard: dead /api/export/companies.csv action removed.
  T1.5 dossier: fetch_state fail-closed on all three dossier reports.
  T1.6 KYC: neutral technical screening copy on intro/filter/empty-state/note;
            no unapproved legal claims; threshold boundary semantics
            (24.99 / 25.00 / 25.01 — filter >= 25.0 is technical behaviour).
"""
import pathlib

import psycopg
import pytest
from psycopg.rows import dict_row

from heimdal.app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# ---------------------------------------------------------------- T1.1 / T1.2

def test_bulk_page_current_naming(client):
    res = client.get("/bulk-screening")
    assert res.status_code == 200
    assert "Bulk Screening" in res.text
    assert "Porteføljeopslag" not in res.text


def test_bulk_api_echoes_never_render_html(client):
    """T1.2: the API may echo raw identifiers (source of truth), the PAGE must
    render them as text — verified in browser (tools/verify_bulk_crafted_input.py).
    Here: API contract sanity — found entities have integer ids and known kinds."""
    res = client.post("/api/bulk/screen", json={"identifiers": ["5396", "IkkeFundetXYZ"]})
    assert res.status_code == 200
    rows = res.json["results"]
    by_q = {r["query"]: r for r in rows}
    assert by_q["5396"]["kind"] == "COMPANY" and isinstance(by_q["5396"]["id"], int)
    assert by_q["IkkeFundetXYZ"]["kind"] == "UNKNOWN" and by_q["IkkeFundetXYZ"]["id"] is None


# ---------------------------------------------------------------- T1.3

def test_watchlist_no_delegated_double_handler(client):
    res = client.get("/overvaagning")
    assert res.status_code == 200
    # the removed delegated listener must be gone
    assert 'button[onclick*="toggleWatchlist"]' not in res.text
    assert 'closest("button[onclick*=\'toggleWatchlist\']")' not in res.text
    # single invocation path: one data-unwatch-id button per row, handled by
    # exactly one delegated listener in static/app.js (G-05: no inline onclick)
    assert "onclick" not in res.text
    js = (pathlib.Path(__file__).parent / "static/app.js").read_text(encoding="utf-8")
    assert js.count("closest('[data-unwatch-id]')") == 1
    # and the old regex-based re-invocation is not re-added
    assert "match(/toggleWatchlist" not in res.text


# ---------------------------------------------------------------- T1.4

def test_dashboard_has_no_dead_global_export(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "/api/export/companies.csv" not in res.text
    assert "Eksportér CSV" not in res.text


# ---------------------------------------------------------------- T1.5

def test_dossier_fail_closed_never_fetched(client):
    res = client.get("/companies/49/dossier")  # B. J. Holding A/S — no provenance row
    assert res.status_code == 200
    assert "KILDE: ALDRIG HENTET DIREKTE" in res.text
    assert "Aldrig hentet direkte" in res.text
    assert "OFFICIELT REGISTERUDSKRIFT" not in res.text
    assert "PERSONLIGT REGISTERUDSKRIFT" not in res.text


def test_dossier_fetched_ok_discloses_snapshot(client):
    res = client.get("/companies/9667/dossier")  # Betri — fetched OK
    assert res.status_code == 200
    assert "KILDE: REGISTER-SNAPSHOT" in res.text
    assert "Hentet fra kilden" in res.text
    assert "OFFICIELT REGISTERUDSKRIFT" not in res.text


def test_dossier_person_fail_closed(client):
    res = client.get("/persons/7412/dossier")
    assert res.status_code == 200
    assert "ALDRIG HENTET" in res.text


# ---------------------------------------------------------------- T1.6

LEGAL_CLAIMS_GONE = [
    "Hvidvasklovens §11",
    "hvidvaskloven",
    "Lovpligtigt",
    "mere end 25,00 %",
    "kapitalen eller stemmerettighederne",
    "OFFICIELT REGISTERUDSKRIFT",
    "ikke i sig selv en juridisk sigtelse",  # keep only the neutral disclaimer below
]


def test_kyc_neutral_screening_copy(client):
    res = client.get("/companies/9667/kyc")
    assert res.status_code == 200
    for bad in ["Hvidvasklovens", "hvidvaskloven", "Lovpligtigt", "mere end 25,00 %",
                "kapitalen eller stemmerettighederne", "OFFICIELT REGISTERUDSKRIFT",
                "Reelle Ejere (UBO", "alternative reelle ledelsespersoner"]:
        assert bad not in res.text, f"unapproved claim still present: {bad!r}"
    # aligned technical filter copy on all three surfaces
    assert "Heimdal-filter: fysiske personer med observeret samlet ejerandel på 25 % eller derover" in res.text
    assert "Denne visning er en teknisk screening baseret på observerede ejerandele og udgør ikke i sig selv en juridisk UBO-klassifikation." in res.text
    assert "Heimdal-filter (teknisk)" in res.text


def test_kyc_threshold_boundary_semantics():
    """Detector per plan T1.6: best_pct = 24.99 / 25.00 / 25.01 — filter and
    copy describe the SAME technical behaviour on all three surfaces."""
    with app.test_request_context():
        entity = {"canonical_entity_id": 1, "display_name": "T", "entity_kind": "COMPANY",
                  "registration_number": "1", "raw_type_token": "T", "entity_kind2": None,
                  "inventory_status": "ACTIVE", "source_entity_id": 1}
        prov = {"last_fetched_at": None, "snapshot_sha256": None,
                "last_http_status": None, "last_error_category": None}
        mk = lambda pct, name: {"person_name": name, "best_pct": pct,
                                "path_type": "DIRECT", "path_desc": "d"}
        html = app.jinja_env.get_template("dossier_kyc.html").render(
            entity=entity, provenance=prov, fetch_state="FETCHED_OK",
            generated_at="01-01-2026 00:00", control=None, roles={},
            structure_status={"has_observations": False, "observation_count": 0, "observations": []},
            group_info=None,
            tree=[mk(24.99, "UNDER"), mk(25.00, "AT25"), mk(25.01, "OVER")])
        assert "UNDER" not in html          # 24.99 filtered out (>= 25.0)
        assert "AT25" in html               # 25.00 included (>= 25.0)
        assert "OVER" in html               # 25.01 included
        assert "Heimdal-filter: fysiske personer med observeret samlet ejerandel på 25 % eller derover" in html
        # empty-state variant uses the same technical description
        html_empty = app.jinja_env.get_template("dossier_kyc.html").render(
            entity=entity, provenance=prov, fetch_state="FETCHED_OK",
            generated_at="01-01-2026 00:00", control=None, roles={},
            structure_status={"has_observations": False, "observation_count": 0, "observations": []},
            group_info=None, tree=[])
        assert "Heimdal-filteret fandt ingen fysiske personer med observeret samlet ejerandel på 25 % eller derover" in html_empty