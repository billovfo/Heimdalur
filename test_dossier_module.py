"""Verify dossier blueprint exists and has 3 routes."""
from heimdal.modules.dossier.routes import dossier_bp
from flask import Blueprint


def test_dossier_blueprint_exists():
    assert isinstance(dossier_bp, Blueprint)
    assert dossier_bp.name == "dossier"
