# heimdal/modules/compliance/routes.py
"""Compliance routes: /compliance, /api/export/compliance/matrix.csv,
/api/export/compliance/matrix.json."""
import math
from flask import Blueprint, jsonify, render_template, request

from heimdal.modules.shared.db import conn
from heimdal.modules.shared.csv_export import csv_response
from heimdal.modules.shared.inputs import page_arg
from heimdal.modules.compliance.repository import (
    structure_observation_summary, structure_observation_matrix,
    registration_patterns,
)

compliance_bp = Blueprint("compliance", __name__)


@compliance_bp.get("/compliance")
def compliance():
    c = conn()
    otype = request.args.get("observation_type")
    q = request.args.get("q", "").strip()
    page_size = 50
    offset = (page_arg() - 1) * page_size

    summary = structure_observation_summary(c)
    matrix = structure_observation_matrix(c, observation_type=otype if otype else None, search=q if q else None, limit=page_size, offset=offset)
    page = matrix["offset"] // page_size + 1  # the repository clamps to the last page
    total_pages = max(1, math.ceil(matrix["total"] / page_size))
    
    return render_template(
        "compliance.html",
        summary=summary,
        rows=matrix["rows"],
        total=matrix["total"],
        page=page,
        total_pages=total_pages,
        observation_type=otype,
        q=q,
        registration_patterns=registration_patterns(c, limit=10),
    )


@compliance_bp.get("/api/export/compliance/matrix.csv")
def export_compliance_matrix_csv():
    c = conn()
    matrix = structure_observation_matrix(c, limit=10000)
    fields = [
        "company_name", "company_regnr", "observation_type", "observation_code",
        "observation_label", "observed_pct", "related_entity_name",
    ]
    return csv_response(matrix["rows"], fields, "heimdal_compliance_matrix.csv")


@compliance_bp.get("/api/export/compliance/matrix.json")
def export_compliance_matrix_json():
    c = conn()
    matrix = structure_observation_matrix(c, limit=10000)
    return jsonify(matrix["rows"])
