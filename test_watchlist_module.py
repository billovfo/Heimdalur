"""Verify watchlist module exists and exports functions."""
from heimdal.modules.watchlist.service import (
    get_watchlist, toggle_watchlist, watchlist_change_feed,
)
from heimdal.modules.watchlist.routes import watchlist_bp
from flask import Blueprint


def test_watchlist_functions_exist():
    assert callable(get_watchlist)
    assert callable(toggle_watchlist)
    assert callable(watchlist_change_feed)


def test_watchlist_blueprint_exists():
    assert isinstance(watchlist_bp, Blueprint)
    assert watchlist_bp.name == "watchlist"
