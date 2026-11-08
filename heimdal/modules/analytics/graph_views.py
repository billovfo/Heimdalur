"""heimdal/modules/analytics/graph_views.py — Phase 5 constellation layer.

Read-only composition on top of the ACTIVE analytics build for the
network graph UX ("Graph/advanced group UX", plan §17). Adds recurring
member-set constellation nodes, membership edges and episode summaries to
the EXISTING ego graph — no new persisted structures, no write paths, no
build semantics change (Phase 3 rows are read as-is).

Contracts carried over unchanged from Phase 4 (B-scope):

* single ACTIVE build pin per response — never SUPERSEDED (B3/B4/B5);
* group identity = (role_context, member_set); CORE/RECURRING twins of
  the same member-set render as ONE constellation node with kind badges
  (same merge rule as the Phase 4 group cards, B23);
* temporal wording reuses the Phase 4 episode renderer — an interval only
  for bounded SAFE_PERIOD_OVERLAP episodes, never for snapshot/event
  episodes (B26/B27/B28);
* ownership remains a point observation — this layer renders no
  ownership period/duration wording (B8);
* coverage comes from the pinned build's own jsonb (B36), web-zero
  wording stays honest (B37);
* no alliance score, no ML scoring, no "Alliance" label (§20/§22/§24).

Fail-closed: no ACTIVE build, both flags off, or any read error -> None
or a degraded error marker; the registry graph itself is never affected.
"""
from __future__ import annotations

import logging

import psycopg

from heimdal.modules.analytics import repository as repo
from heimdal.modules.analytics.views import (
    ANALYTICS_DISCLAIMER,
    ROLE_CONTEXT_LABELS,
    _dk_date,
    _decorate_groups,
    _merge_core_recurring,
    episode_render_text,
    get_active_analytics_build,
)

_logger = logging.getLogger(__name__)

# Constellation display cap (deterministic top-K, stated total). Sizing
# re-measured on build 13: a person may belong to 130 groups; a company may
# host 239 observed groups — an uncapped ring would be unusable. The cap
# mirrors the existing ego_layout truncation convention (stated true total,
# never silent).
CONSTELLATION_CAP = 8
CONSTELLATION_NODE_KIND = "CONSTELLATION"

CONSTELLATION_DISCLAIMER = (
    "Konstellationer er udledte fællesskaber fra analyse-buildet "
    "(understøttede co-participation episoder) — ikke en kilde-iagttagelse "
    "og ikke en alliance- eller venskabsvurdering.")


def _support_key(entry):
    s = entry.get("support") or {}
    return (s.get("support_episode_count") or 0,
            s.get("support_company_count") or 0,
            entry.get("group_size") or 0)


def _constellation_sort_key(entry):
    """Deterministic order: strongest support first, then larger groups,
    then stable member-set/context identity. No score — ordering is a
    documented presentation rule over Phase 3 support counts only."""
    return (-_support_key(entry)[0], -_support_key(entry)[1],
            -_support_key(entry)[2], entry["member_set_hash"],
            entry["context"], entry["group_id"])


def _constellation_sort_key_from_node(node):
    """Recompute the presentation order from a rendered node (test
    detector for the deterministic-cap invariant). Weaker proxy of the
    production key: the member_set_hash tie-break is omitted because the
    node does not carry it — group_id is unique, so the ordering stays
    total and I9 determinism holds."""
    s = node.get("support") or {}
    return (-(s.get("support_episode_count") or 0),
            -(s.get("support_company_count") or 0),
            -(node.get("group_size") or 0),
            node.get("context") or "", node.get("group_id"))


def _constellation_nodes(entry):
    """One constellation node + its membership edges from a merged group
    view. Node id is synthetic (g<group_id>) and never collides with
    canonical entity ids. Pure function of the merged entry — no DB
    access, no build dependency."""
    node_id = f"g{entry['group_id']}"
    node = {
        "node_id": node_id,
        "group_id": entry["group_id"],
        "kind": CONSTELLATION_NODE_KIND,
        "kind_labels": entry.get("kinds_labels") or [entry.get("group_kind_label")],
        "context": entry["context"],
        "context_label": entry.get("context_label"),
        "group_size": entry["group_size"],
        "support": entry.get("support"),
        "first_observed": entry.get("first_observed"),
        "last_observed": entry.get("last_observed"),
        "episode_kinds_labels": entry.get("episode_kinds_labels") or [],
        "members": [{"canonical_person_id": m["canonical_person_id"],
                     "name": m.get("display_name")}
                    for m in entry.get("members") or []],
    }
    edges = []
    for m in entry.get("members") or []:
        pid = m.get("canonical_person_id")
        if pid is None:
            continue
        edges.append({"from_id": node_id, "to_id": pid,
                      "kind": "CONSTELLATION_MEMBER",
                      "label": "medlem af konstellation"})
    for comp in entry.get("companies") or []:
        cid = comp.get("canonical_company_id")
        if cid is None:
            continue
        edges.append({"from_id": node_id, "to_id": cid,
                      "kind": "CONSTELLATION_EPISODE",
                      "label": "episode hos"})
    return node, edges


def _episode_summaries(conn, build, entries):
    """Batched episode summaries for the shown constellations only —
    same queries/shape as the Phase 4 evidence drilldown, no per-group
    N+1. Temporal wording comes exclusively from episode_render_text."""
    group_ids = [e["group_id"] for e in entries]
    if not group_ids:
        return {}
    try:
        observations = repo.group_observations(conn, build["build_run_id"],
                                               group_ids)
    except psycopg.Error as exc:
        _logger.warning("constellation episodes read degraded: %s", repr(exc))
        return {e["group_id"]: [] for e in entries}
    names = repo.names_for(conn, {o["canonical_company_id"] for o in observations})
    by_group = {}
    for o in observations:
        by_group.setdefault(o["group_id"], []).append(o)
    out = {}
    for e in entries:
        eps = []
        for o in by_group.get(e["group_id"], []):
            eps.append({
                "canonical_company_id": o["canonical_company_id"],
                "company_name": (names.get(o["canonical_company_id"]) or {}).get(
                    "display_name"),
                "context": o["role_context"],
                "context_label": ROLE_CONTEXT_LABELS.get(o["role_context"],
                                                         o["role_context"]),
                "co_observation_kind": o["co_observation_kind"],
                "episode_start": _dk_date(o["episode_start"]),
                "episode_end": _dk_date(o["episode_end"]),
                "render_text": episode_render_text({
                    "co_observation_kind": o["co_observation_kind"],
                    "episode_start": _dk_date(o["episode_start"]),
                    "episode_end": _dk_date(o["episode_end"]),
                }),
            })
        out[e["group_id"]] = eps
    return out


def constellation_layer(conn, entity_kind, canonical_entity_id, build=None,
                        limit=CONSTELLATION_CAP):
    """Constellation nodes/edges for one entity's network page.

    Returns None when analytics is off, there is no ACTIVE build, or the
    entity has no supported groups — the page then renders exactly the
    pre-Phase-5 graph. A read error degrades to an error marker only.
    """
    build = build or get_active_analytics_build(conn)
    if build is None:
        return None
    try:
        if entity_kind == "PERSON":
            rows = repo.groups_for_person(conn, build["build_run_id"],
                                          canonical_entity_id)
        elif entity_kind == "COMPANY":
            rows = repo.groups_for_company(conn, build["build_run_id"],
                                           canonical_entity_id)
        else:
            return None
        if not rows:
            return None
        decorated = _decorate_groups(conn, build, rows)
    except psycopg.Error as exc:
        _logger.warning("constellation layer degraded: %s", repr(exc))
        return {"build_run_id": build["build_run_id"],
                "content_hash": build["content_hash"], "error": True,
                "nodes": [], "edges": []}
    merged = _merge_core_recurring(decorated)
    merged.sort(key=_constellation_sort_key)
    total = len(merged)
    shown = merged[:limit]
    episodes = _episode_summaries(conn, build, shown)
    nodes, edges = [], []
    for entry in shown:
        node, node_edges = _constellation_nodes(entry)
        node["episodes"] = episodes.get(entry["group_id"], [])
        nodes.append(node)
        edges.extend(node_edges)
    return {"build_run_id": build["build_run_id"],
            "content_hash": build["content_hash"],
            "coverage": build.get("coverage_display"),
            "nodes": nodes, "edges": edges,
            "total": total, "shown": len(shown),
            "cap": limit,
            "disclaimer": ANALYTICS_DISCLAIMER,
            "constellation_disclaimer": CONSTELLATION_DISCLAIMER}


def graph_network_payload(conn, entity, graph, build=None):
    """Versioned read-only graph API payload (plan §19 shape): registry
    ego graph untouched + analytics constellation section with a single
    ACTIVE build pin and coverage from the pinned build. `graph` is the
    existing ego_graph() result — this function never re-queries it.
    The caller resolves the ACTIVE build (and fail-closes on None); the
    same pin is threaded into the constellation layer so the response
    can never mix pins."""
    from heimdal.modules.entities.service import ego_layout
    layer = constellation_layer(conn, entity["entity_kind"],
                                entity["canonical_entity_id"], build=build)
    layout = ego_layout(graph)
    payload = {
        "entity": {"canonical_entity_id": entity["canonical_entity_id"],
                   "display_name": entity["display_name"],
                   "entity_kind": entity["entity_kind"]},
        "registry": {"nodes": layout["nodes"], "edges": layout["edges"],
                     "total_nodes": layout["total_nodes"],
                     "shown_count": layout["shown_count"],
                     "truncated": layout["truncated"]},
    }
    if layer is not None:
        payload["analytics"] = layer
    return payload