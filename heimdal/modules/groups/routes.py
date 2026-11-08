# heimdal/modules/groups/routes.py
"""Groups routes: /groups, /groups/<id>, /api/groups/<id>/flow."""
from flask import Blueprint, abort, jsonify, render_template

from heimdal.modules.shared.db import conn
from heimdal.modules.shared.inputs import clamp_offset, page_arg
from heimdal.modules.groups.service import (
    all_corporate_groups, corporate_group_tree, corporate_group_flow,
)

groups_bp = Blueprint("groups", __name__)


@groups_bp.get("/groups")
def groups_overview():
    """T5.3 (F-16): search + pagination so groups past row 100 are reachable.
    Read-only params; default page size unchanged (100)."""
    from flask import request
    from heimdal.modules.groups import repository
    c = conn()
    q = (request.args.get("q") or "").strip() or None
    per_page = 100
    total = repository.count_all_corporate_groups(c, q=q)
    total_n = total["n"] if isinstance(total, dict) else total[0]
    offset = clamp_offset((page_arg() - 1) * per_page, total_n, per_page)
    page = offset // per_page + 1
    groups = all_corporate_groups(c, limit=per_page, offset=offset, q=q)
    return render_template(
        "groups.html", groups=groups, page=page,
        total_pages=max(1, (total_n + per_page - 1) // per_page),
        total_groups=total_n, q=q or "", per_page=per_page)


@groups_bp.get("/groups/<int:canonical_entity_id>")
def group_detail(canonical_entity_id):
    c = conn()
    tree = corporate_group_tree(c, canonical_entity_id)
    if not tree:
        abort(404)
    return render_template("group_detail.html", group=tree)


@groups_bp.get("/api/groups/<int:canonical_entity_id>/flow")
def api_group_flow(canonical_entity_id):
    c = conn()
    flow = corporate_group_flow(c, canonical_entity_id)
    if not flow:
        return jsonify({"error": "Entity not found"}), 404
    return jsonify(flow)
