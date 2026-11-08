# test_geo_module.py
"""Verify geo module exists and has blueprint."""
from heimdal.config import openstreetmap_tile_url
from heimdal.modules.geo.service import geo_map_data
from heimdal.modules.geo.routes import geo_bp
from flask import Blueprint


def test_geo_function_exists():
    assert callable(geo_map_data)


def test_geo_blueprint_exists():
    assert isinstance(geo_bp, Blueprint)
    assert geo_bp.name == "geo"


def test_openstreetmap_tile_url_has_no_environment_dependency(monkeypatch):
    monkeypatch.setenv("MAPTILER_API_KEY", "must-not-be-used")
    assert openstreetmap_tile_url() == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
