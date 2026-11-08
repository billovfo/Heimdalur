# heimdal/modules/geo/routes.py
"""Geo routes: /geo-kort, /api/geo/clusters, /api/geo/companies."""
import psycopg
from flask import Blueprint, abort, jsonify, render_template, request, url_for

from heimdal.config import openstreetmap_tile_url
from heimdal.modules.shared.db import conn
from heimdal.modules.geo.service import geo_company_list, geo_map_data

geo_bp = Blueprint("geo", __name__)


@geo_bp.errorhandler(psycopg.errors.UndefinedTable)
def _geo_tables_missing(_e):
    # G-01: geo tables missing is an outage, never an empty map or demo data
    return "Placeringsdata er ikke bygget i denne database.", 503


@geo_bp.get("/geo-kort")
def geo_map_page():
    c = conn()
    return render_template(
        "geo_map.html",
        map_data=geo_map_data(c),
        tile_url=openstreetmap_tile_url(),
    )


@geo_bp.get("/api/geo/clusters")
def api_geo_clusters():
    return jsonify(geo_map_data(conn()))


@geo_bp.get("/api/geo/companies")
def api_geo_companies():
    c = conn()
    try:
        companies = geo_company_list(
            c, city=request.args.get("city"), region=request.args.get("region"))
    except ValueError:
        abort(400)
    return jsonify(companies=[
        {"name": row["display_name"],
         "url": url_for("entities.company", canonical_entity_id=row["canonical_entity_id"])}
        for row in companies
    ])
