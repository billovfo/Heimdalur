# heimdal/modules/graph/routes.py
"""Graph routes: /find-forbindelse, /api/pathfinder,
/companies/<id>/network, /persons/<id>/network,
/api/network/<id>, /api/analytics/graph/<id> (Phase 5, gated)."""
from flask import Blueprint, abort, jsonify, redirect, render_template, request

from heimdal.modules.shared.db import conn, read_savepoint
from heimdal.modules.shared.entity import entity, provenance, fetch_state, entity_url
from heimdal.modules.shared.templates import pct
from heimdal.modules.entities.service import (
    ego_graph, ego_layout, network_layout_with_constellations)
from heimdal.modules.graph.repository import GraphRepository
from heimdal.modules.graph.service import find_entity_paths, get_entity_network_graph

graph_bp = Blueprint("graph", __name__)


def _constellations(c, ent):
    """Phase 5 constellation layer for a network page — gated, fail-closed.

    Returns None unless BOTH REGISTRY_ANALYTICS_ENABLED and
    REGISTRY_ANALYTICS_GRAPH_ENABLED are on, the entity is person/company,
    and an ACTIVE analytics build resolves. Any error inside the layer
    degrades to its own error marker — the registry graph never changes.
    """
    try:
        from heimdal.modules.analytics.flags import analytics_graph_enabled
        if not analytics_graph_enabled() or ent["entity_kind"] not in (
                "PERSON", "COMPANY"):
            return None
        from heimdal.modules.analytics import graph_views
        with read_savepoint(c):
            return graph_views.constellation_layer(c, ent["entity_kind"],
                                                   ent["canonical_entity_id"])
    except Exception:  # noqa: BLE001 — the graph page must never break
        return None


@graph_bp.get("/find-forbindelse")
def pathfinder():
    c = conn()
    from_id = request.args.get("from", type=int)
    to_id = request.args.get("to", type=int)
    result = None
    from_entity = entity(c, from_id) if from_id else None
    to_entity = entity(c, to_id) if to_id else None

    if from_id and to_id:
        result = find_entity_paths(GraphRepository(c), from_id, to_id)

    return render_template(
        "pathfinder.html",
        from_id=from_id,
        to_id=to_id,
        from_entity=from_entity,
        to_entity=to_entity,
        result=result
    )


@graph_bp.get("/api/pathfinder")
def api_pathfinder():
    from_raw = request.args.get("from")
    to_raw = request.args.get("to")
    if not from_raw or not to_raw:
        return jsonify({"error": "Both 'from' and 'to' parameters are required"}), 400
    from_id = request.args.get("from", type=int)
    to_id = request.args.get("to", type=int)
    if from_id is None or to_id is None:
        return jsonify({
            "error": "'from' and 'to' must be valid integer entity IDs"
        }), 400
    c = conn()
    result = find_entity_paths(GraphRepository(c), from_id, to_id)
    return jsonify(result)


@graph_bp.get("/companies/<int:canonical_entity_id>/network")
def company_network(canonical_entity_id):
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None or ent["entity_kind"] == "PERSON":
        abort(404)
    if ent["entity_kind"] != "COMPANY":
        # §3.4: the fallback kinds have no full portal and therefore no §21
        # network graph either — redirect to their minimal page.
        return redirect(entity_url(ent["entity_kind"], canonical_entity_id))
    prov = provenance(c, canonical_entity_id)
    graph = ego_graph(c, canonical_entity_id, ent["source_entity_id"])
    constellations = _constellations(c, ent)
    if constellations is not None:
        layout = network_layout_with_constellations(graph, constellations)
    else:
        layout = ego_layout(graph)
    return render_template("network.html", entity=ent, layout=layout,
                           constellations=constellations,
                           fetch_state=fetch_state(prov))


@graph_bp.get("/persons/<int:canonical_entity_id>/network")
def person_network(canonical_entity_id):
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None or ent["entity_kind"] != "PERSON":
        abort(404)
    prov = provenance(c, canonical_entity_id)
    graph = ego_graph(c, canonical_entity_id, ent["source_entity_id"])
    constellations = _constellations(c, ent)
    if constellations is not None:
        layout = network_layout_with_constellations(graph, constellations)
    else:
        layout = ego_layout(graph)
    return render_template("network.html", entity=ent, layout=layout,
                           constellations=constellations,
                           fetch_state=fetch_state(prov))


@graph_bp.get("/api/analytics/graph/<int:canonical_entity_id>")
def api_analytics_graph(canonical_entity_id):
    """Phase 5: versioned read-only graph API. Registry ego graph section
    (unchanged data) + analytics constellation section pinned to the
    single ACTIVE build. Fail-closed: 404 unless both flags are on, the
    entity exists, and an ACTIVE build resolves — never a SUPERSEDED
    fallback, and never a pin-less 200 on an analytics-namespaced
    endpoint (no-ACTIVE-build is indistinguishable from an error)."""
    try:
        from heimdal.modules.analytics.flags import analytics_graph_enabled
        gate = analytics_graph_enabled()
    except Exception:  # noqa: BLE001
        gate = False
    if not gate:
        abort(404)
    c = conn()
    ent = entity(c, canonical_entity_id)
    if ent is None or ent["entity_kind"] not in ("PERSON", "COMPANY"):
        abort(404)
    try:
        from heimdal.modules.analytics.views import get_active_analytics_build
        from heimdal.modules.analytics import graph_views
        build = get_active_analytics_build(c)
        if build is None:
            abort(404)  # no ACTIVE build — fail closed, never pin-less
        graph = ego_graph(c, canonical_entity_id, ent["source_entity_id"])
        payload = graph_views.graph_network_payload(c, ent, graph, build=build)
    except Exception:  # noqa: BLE001
        abort(404)
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@graph_bp.get("/api/network/<int:canonical_entity_id>")
def api_network(canonical_entity_id):
    min_pct = request.args.get("min_pct", 0.0, type=float)
    c = conn()
    graph = get_entity_network_graph(GraphRepository(c), canonical_entity_id, min_pct=min_pct)
    if not graph:
        return jsonify({"error": "Entity not found"}), 404
    resp = jsonify(graph)
    resp.headers["Cache-Control"] = "private, max-age=60"  # carries personal data (F-10)
    return resp
