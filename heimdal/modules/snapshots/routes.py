"""Snapshots routes: /aendringer."""
import math
from flask import Blueprint, jsonify, render_template, request

from heimdal.modules.shared.db import conn
from heimdal.modules.shared.inputs import clamp_offset, date_arg, page_arg
from heimdal.modules.snapshots.service import entity_change_feed

snapshots_bp = Blueprint("snapshots", __name__)


@snapshots_bp.get("/aendringer")
def changelog():
    cat = request.args.get("category")
    from_date = date_arg("from_date")  # F-6: not an ISO date -> 400, never a SQL cast error
    to_date = date_arg("to_date")
    c = conn()
    page_size = 50
    # total for pagination + header meta (same WHERE uden limit/offset)
    clauses = []
    params = []
    if cat:
        clauses.append("change_category = %s"); params.append(cat)
    if from_date:
        clauses.append("event_timestamp >= %s"); params.append(from_date)
    if to_date:
        clauses.append("event_timestamp <= %s"); params.append(to_date + " 23:59:59")
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    row = c.execute(f"SELECT count(*) FROM v_entity_change_feed {where_sql}", params).fetchone() if clauses else c.execute("SELECT count(*) FROM v_entity_change_feed").fetchone()
    total = row["count"] if isinstance(row, dict) else row[0]
    total_pages = max(1, math.ceil(total / page_size))
    offset = clamp_offset((page_arg() - 1) * page_size, total, page_size)
    page = offset // page_size + 1
    events = entity_change_feed(c, category=cat if cat else None, from_date=from_date,
                                to_date=to_date, limit=page_size, offset=offset)
    return render_template(
        "changelog.html", 
        events=events, 
        page=page, 
        total_pages=total_pages,
        total=total,
        category=cat,
        from_date=from_date,
        to_date=to_date
    )

