"""Export routes: /api/export/company/<id>/owners.csv,
/api/export/company/<id>/ejarayvirlit.csv."""
from flask import Blueprint, abort

from heimdal.modules.shared.db import conn
from heimdal.modules.shared.entity import entity
from heimdal.modules.shared.csv_export import csv_response
from heimdal.modules.entities.service import owners_of, owner_chain_rows, person_owners_tree

export_bp = Blueprint("export", __name__)


@export_bp.get("/api/export/company/<int:canonical_entity_id>/owners.csv")
def export_company_owners_csv(canonical_entity_id):
    c = conn()
    ent = entity(c, canonical_entity_id)
    if not ent:
        abort(404)
    rows = owners_of(c, canonical_entity_id)
    fields = ["owner_name", "owner_kind", "parsed_percentage", "raw_percentage_label", "semantic_direction_confidence"]
    return csv_response(rows, fields, f"heimdal_company_{canonical_entity_id}_owners.csv")


@export_bp.get("/api/export/company/<int:canonical_entity_id>/ejarayvirlit.csv")
def export_ejarayvirlit_csv(canonical_entity_id):
    c = conn()
    ent = entity(c, canonical_entity_id)
    if not ent:
        abort(404)
    tree = owner_chain_rows(person_owners_tree(c, canonical_entity_id, ent["source_entity_id"]))
    fields = ["person_name", "best_pct", "path_type", "path_desc"]
    return csv_response(tree, fields, f"heimdal_company_{canonical_entity_id}_ejarayvirlit.csv")
