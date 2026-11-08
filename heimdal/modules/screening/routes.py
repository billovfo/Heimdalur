"""Screening routes: /bulk-screening, /api/bulk/screen."""
from flask import Blueprint, jsonify, render_template

from heimdal.modules.shared.db import conn
from heimdal.modules.shared.inputs import json_object
from heimdal.modules.screening.service import batch_screen_entities

screening_bp = Blueprint("screening", __name__)

MAX_IDENTIFIERS = 200  # F-5: one request must stay one small batch


@screening_bp.get("/bulk-screening")
def bulk_screening_page():
    return render_template("bulk_screening.html")


@screening_bp.post("/api/bulk/screen")
def api_bulk_screen():
    identifiers = json_object().get("identifiers")
    if (not isinstance(identifiers, list) or len(identifiers) > MAX_IDENTIFIERS
            or not all(isinstance(i, str) and "\x00" not in i for i in identifiers)):
        return jsonify({"error": f"'identifiers' skal være en liste med højst {MAX_IDENTIFIERS}"
                                 " tekster"}), 400
    return jsonify({"results": batch_screen_entities(conn(), identifiers)})
