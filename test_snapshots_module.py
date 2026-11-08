"""Verify snapshots module exists and exports functions."""
from heimdal.modules.snapshots.service import (
    entity_change_feed, entity_timeline,
)
from heimdal.modules.snapshots.routes import snapshots_bp
from flask import Blueprint


def test_snapshots_functions_exist():
    assert callable(entity_change_feed)
    assert callable(entity_timeline)


def test_snapshots_blueprint_exists():
    assert isinstance(snapshots_bp, Blueprint)
    assert snapshots_bp.name == "snapshots"
