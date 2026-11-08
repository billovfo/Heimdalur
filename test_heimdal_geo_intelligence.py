#!/usr/bin/env python3
"""Tests for Faroese Geo-Intelligence & Regional Clusters."""
import pytest
import psycopg
from psycopg.rows import dict_row
from heimdal.app import app
from heimdal.config import DB_URI

def conn():
    return psycopg.connect(DB_URI, row_factory=dict_row)

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_geo_map_page_renders(client):
    res = client.get("/geo-kort")
    assert res.status_code == 200
    assert "Geo-Kort" in res.text or "Geografisk" in res.text

def test_city_aggregate_contains_no_entity_or_address_fields(client):
    response = client.get("/api/geo/clusters")
    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {"cities", "coverage"}
    assert set(body["coverage"]) == {"faroe_islands", "unknown", "foreign"}
    assert body["cities"]
    assert all(set(city) == {"city", "region", "latitude", "longitude", "company_count"}
               for city in body["cities"])
    assert all(city["company_count"] > 0 for city in body["cities"])
    assert all(isinstance(city["latitude"], float) and isinstance(city["longitude"], float)
               for city in body["cities"])
    assert all("address" not in city and "entity" not in city for city in body["cities"])


def test_geo_api_ignores_removed_sector_parameter(client):
    assert client.get("/api/geo/clusters?sector=FISHING").get_json() == \
        client.get("/api/geo/clusters").get_json()


def test_geo_company_list_returns_only_names_and_urls(client):
    city = client.get("/api/geo/clusters").get_json()["cities"][0]["city"]
    response = client.get("/api/geo/companies", query_string={"city": city})
    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {"companies"}
    assert body["companies"]
    assert all(set(item) == {"name", "url"} for item in body["companies"])
    assert [item["name"] for item in body["companies"]] == sorted(item["name"] for item in body["companies"])
    assert all(item["url"].startswith("/companies/") for item in body["companies"])


def test_geo_company_list_rejects_invalid_selection(client):
    for query in ({}, {"city": "Tórshavn", "region": "SUDURSTREYMOY"}, {"city": "unknown"}):
        assert client.get("/api/geo/companies", query_string=query).status_code == 400


def test_geo_company_list_region_count_matches_clusters(client):
    clusters = client.get("/api/geo/clusters").get_json()["cities"]
    by_region = {}
    for city in clusters:
        by_region[city["region"]] = by_region.get(city["region"], 0) + city["company_count"]
    region, expected = next(iter(by_region.items()))
    response = client.get("/api/geo/companies", query_string={"region": region})
    assert response.status_code == 200
    assert len(response.get_json()["companies"]) == expected
