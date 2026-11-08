"""Verify search module exists and has blueprint."""
from heimdal.modules.search.routes import search_bp
from flask import Blueprint


def test_search_blueprint_exists():
    assert isinstance(search_bp, Blueprint)
    assert search_bp.name == "search"
