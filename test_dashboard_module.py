"""Verify dashboard module exists and exports functions."""
from heimdal.modules.dashboard.service import (
    dashboard_charts, dashboard_macro_stats, top_lists,
)
from heimdal.modules.dashboard.routes import dashboard_bp
from flask import Blueprint


def test_dashboard_functions_exist():
    assert callable(dashboard_charts)
    assert callable(dashboard_macro_stats)
    assert callable(top_lists)


def test_dashboard_blueprint_exists():
    assert isinstance(dashboard_bp, Blueprint)
    assert dashboard_bp.name == "dashboard"
