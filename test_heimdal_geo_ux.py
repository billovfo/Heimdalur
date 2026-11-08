#!/usr/bin/env python3
"""Tests for Geo-Intelligence Sector Filtering & UX."""
from pathlib import Path
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_geo_clusters_sector_filtering(client):
    res = client.get("/api/geo/clusters?sector=FISHING")
    assert res.status_code == 200
    data = res.json
    assert set(data) == {"cities", "coverage"}
    assert data == client.get("/api/geo/clusters").get_json()

def test_geo_page_has_city_and_region_filters(client):
    html = client.get("/geo-kort").text
    assert 'id="geo-city-search"' not in html  # removed in e6ab009 (H-7)
    assert 'data-geo-view="region"' in html
    assert "SUDUROY" in html and "NORDOYGGJAR" in html
    assert 'src="/static/vendor/leaflet/leaflet.js"' in html
    assert 'href="/static/vendor/leaflet/leaflet.css"' in html
    assert 'id="geo-table-body"' in html
    assert "sector-btn" not in html
    assert "capital" not in html.lower()
    assert "MapTiler" not in html


def test_geo_page_uses_openstreetmap_images_only(client):
    csp = client.get("/geo-kort").headers["Content-Security-Policy"]
    assert "img-src 'self' data: https://tile.openstreetmap.org" in csp
    assert "maptiler" not in csp.lower()
    assert "https://unpkg.com" not in csp


def test_geo_page_meta_referrer_policy_origin(client):
    html = client.get("/geo-kort").text
    head = html.split("<head>", 1)[1].split("</head>", 1)[0]
    assert '<meta name="referrer" content="origin">' in head


def test_geo_map_js_tooltip_uses_textcontent():
    js = (Path(__file__).parent / "static" / "geo_map.js").read_text()
    assert "tooltip.textContent" in js
    assert "bindTooltip(`" not in js


def test_geo_page_has_direct_city_region_buttons_and_selected_company_table(client):
    html = client.get("/geo-kort").text
    assert 'data-geo-view="city"' in html
    assert 'data-geo-view="region"' in html
    assert 'id="geo-view-mode"' not in html
    assert 'id="geo-selected-companies"' in html
    assert "Vælg en by eller region på kortet for at se virksomheder." in html


def test_geo_map_uses_smaller_city_and_region_bubbles():
    # H-7: since e6ab009 the radius is scaled to the largest marker and capped at
    # 8..32 px; the old unbounded sqrt(count) * 1.75 drew Tórshavn (1730) at 73 px.
    source = (Path(__file__).parent / "static" / "geo_map.js").read_text()
    assert "return 8 + 24 * Math.sqrt(Math.min(count, max) / max);" in source
    assert "Math.sqrt(count) * 1.75" not in source
    assert "REGION_ANCHORS" not in source
    assert "SUDURSTREYMOY: [62.01014, -6.77157]" in source


def test_geo_map_region_rows_use_plain_text_not_company_link():
    source = Path("static/geo_map.js").read_text()
    assert "const isRegion = this.viewMode === 'region'" in source
    assert "if (isRegion) {" in source
    assert "tdCity.textContent = city.city" in source
    assert "link.href = `/companies?q=" in source


def test_geo_map_computes_region_centres_and_loads_selected_companies():
    source = Path("static/geo_map.js").read_text()
    assert "const REGION_ANCHORS" not in source
    assert "SUDURSTREYMOY: [62.01014, -6.77157]" in source
    assert "fetch(`/api/geo/companies?${params}`, { signal: controller.signal })" in source
    assert "textContent" in source


def test_geo_map_cancels_stale_company_fetch():
    source = Path("static/geo_map.js").read_text()
    assert "AbortController" in source
    assert ".abort()" in source


def test_geo_map_has_no_city_search_since_it_was_removed():
    # H-7: e6ab009 removed the city search and region filter from the page and
    # the JS, so a mode switch has no query to clear (it resets the selection,
    # see test_geo_map_mode_switch_aborts_and_resets_selection).
    root = Path(__file__).parent
    source = (root / "static" / "geo_map.js").read_text()
    html = (root / "templates" / "geo_map.html").read_text()
    for gone in ("geo-city-search", "geo-region-filter", "this.query"):
        assert gone not in source and gone not in html


def test_geo_map_mode_switch_aborts_and_resets_selection():
    source = Path("static/geo_map.js").read_text()
    set_view = source.split("setViewMode(viewMode) {", 1)[1]
    assert "if (viewMode === this.viewMode) return;" in set_view
    assert "this.resetSelection()" in set_view


def test_geo_map_reset_selection_clears_inspector_and_list_to_instruction_state():
    source = Path("static/geo_map.js").read_text()
    reset = source.split("resetSelection() {", 1)[1]
    assert ".abort()" in reset
    assert "geo-inspector-city" in reset
    assert "geo-inspector-count" in reset
    assert "geo-inspector-region" in reset
    assert "geo-selected-status" in reset
    assert "geo-selected-body" in reset
    assert "Vælg en region på kortet" in reset
    assert "Vælg en by eller region på kortet for at se virksomheder." in reset


def test_geo_map_passes_abort_signal_to_fetch():
    source = Path("static/geo_map.js").read_text()
    assert "signal: controller.signal" in source


def test_geo_map_reports_selected_company_count_with_plural_form():
    source = Path("static/geo_map.js").read_text()
    render = source.split("renderSelectedCompanies(companies) {", 1)[1]
    assert "=== 1" in render
    assert "virksomhed fundet" in render
    assert "virksomheder fundet" in render
