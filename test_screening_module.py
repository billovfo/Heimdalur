"""Verify screening module exists and has blueprint."""
from heimdal.modules.screening.service import batch_screen_entities
from heimdal.modules.screening.routes import screening_bp
from flask import Blueprint


def test_screening_function_exists():
    assert callable(batch_screen_entities)


def test_screening_blueprint_exists():
    assert isinstance(screening_bp, Blueprint)
    assert screening_bp.name == "screening"
