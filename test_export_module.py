"""Verify export module exists and has blueprint."""
from heimdal.modules.export.routes import export_bp
from flask import Blueprint


def test_export_blueprint_exists():
    assert isinstance(export_bp, Blueprint)
    assert export_bp.name == "export"
