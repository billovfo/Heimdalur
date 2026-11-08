"""Watchlist routes: /overvaagning, /api/watchlist/toggle, /api/watchlist."""
from flask import Blueprint, jsonify, render_template

from heimdal.modules.shared.db import conn
from heimdal.modules.shared.inputs import json_object
from heimdal.modules.watchlist.service import (
    get_watchlist, toggle_watchlist, watchlist_change_feed,
)

watchlist_bp = Blueprint("watchlist", __name__)


@watchlist_bp.get("/overvaagning")
def watchlist_page():
    c = conn()
    watched = get_watchlist(c)
    events = watchlist_change_feed(c)
    return render_template("watchlist.html", watched_entities=watched, events=events)


@watchlist_bp.post("/api/watchlist/toggle")
def api_watchlist_toggle():
    entity_id = json_object().get("canonical_entity_id")
    if type(entity_id) is not int:  # F-14: not "abc", 1.5, true or "1"; entity_kind is ignored
        return jsonify({"error": "canonical_entity_id must be a JSON integer"}), 400
    status = toggle_watchlist(conn(), entity_id)
    if status is None:
        return jsonify({"error": "Entity not found"}), 404
    return jsonify({"status": status, "canonical_entity_id": entity_id})


@watchlist_bp.get("/api/watchlist")
def api_watchlist():
    c = conn()
    return jsonify(get_watchlist(c))
