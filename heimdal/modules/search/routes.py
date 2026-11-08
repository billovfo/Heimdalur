"""Search route: /api/search."""
from flask import Blueprint, jsonify, request

from heimdal.modules.shared.db import conn
from heimdal.modules.shared.entity import entity_url
from heimdal.modules.search import repository

search_bp = Blueprint("search", __name__)


@search_bp.get("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})
    c = conn()
    results = []
    normalized = q.lstrip("Ss#").strip() if q else ""
    regnr_like = f"%{normalized}%" if normalized else None
    like = f"%{q}%"
    prefix_like = f"{q}%"
    if q.isdecimal():  # F-6: "²" is isdigit() but not int()
        rows = repository.search_entities_digit(c, q, regnr_like, like)
    else:
        rows = repository.search_entities_text(c, regnr_like, like, prefix_like)
    for r in rows:
        results.append({
            "id": r["canonical_entity_id"],
            "name": r["display_name"],
            "kind": r["entity_kind"],
            "regnr": r["registration_number"],
            "status": r["inventory_status"],
            "url": entity_url(r["entity_kind"], r["canonical_entity_id"])
        })
    return jsonify({"results": results})
