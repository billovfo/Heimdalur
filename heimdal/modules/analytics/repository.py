"""Registry analytics repository — every SQL the Phase 4 portal may run.

READ-ONLY: this module contains exactly four SQL verbs (SELECT, WITH, VALUES
and nothing mutating). Every query takes the pinned build_run_id resolved by
active_build() — a request never mixes builds (task B3/B4).

Conventions:
* connections use dict_row (heimdal/modules/shared/db.py convention);
* every list query has an explicit deterministic ORDER BY and a LIMIT;
* names are batched per response, never one lookup per row (task B41).
"""
from __future__ import annotations

import psycopg

SCHEMA = "registry_analytics"

# Explicit column lists — no SELECT *, no accidental raw-json leaks.
_BUILD_COLS = ("build_run_id, content_hash, built_at, coverage")
_PPO_COLS = ("pair_id, build_run_id, person_a_id, person_b_id,"
             " canonical_company_id, role_context, co_participation_class,"
             " observation_count, episode_count, known_overlap_days,"
             " first_observed, last_observed, same_source_event_count,"
             " same_snapshot_count, source_family, authority_class")
_OBS_COLS = ("observation_id, build_run_id, canonical_person_id,"
             " canonical_company_id, role_context, source_family,"
             " authority_class, observation_kind, observed_at, action,"
             " ownership_percentage, source_document_id, source_event_id,"
             " source_history_event_id, source_relation_id, source_attempt_id,"
             " source_projection_run_id, identity_resolution")
_PERIOD_COLS = ("period_id, build_run_id, canonical_person_id,"
                " canonical_company_id, role_context, period_start, period_end,"
                " start_status, end_status, period_status, source_family")
_GROUP_COLS = ("group_id, build_run_id, member_set_hash, role_context,"
               " group_kind, group_size, support, first_observed, last_observed")
_GOB_COLS = ("group_observation_id, group_id, canonical_company_id,"
             " role_context, episode_key, episode_start, episode_end,"
             " source_family, co_observation_kind")


def active_build(conn):
    """The single ACTIVE analytics build, or None (fail-closed caller)."""
    return conn.execute(
        f"SELECT {_BUILD_COLS} FROM {SCHEMA}.analytics_build"
        " WHERE status = 'ACTIVE' LIMIT 1").fetchone()


def build_by_id(conn, build_run_id):
    return conn.execute(
        f"SELECT {_BUILD_COLS} FROM {SCHEMA}.analytics_build"
        " WHERE build_run_id = %s", (build_run_id,)).fetchone()


def superseded_build_ids(conn, limit=5):
    return conn.execute(
        f"SELECT build_run_id FROM {SCHEMA}.analytics_build"
        " WHERE status <> 'ACTIVE' ORDER BY build_run_id DESC LIMIT %s",
        (limit,)).fetchall()


# --- pairs -------------------------------------------------------------------

def pair_rows_for_person(conn, build_run_id, person_id):
    """All pair rows touching one person in the pinned build (query-time
    pair-global metrics; bounded by the person's real degree)."""
    return conn.execute(
        f"SELECT {_PPO_COLS} FROM {SCHEMA}.person_person_overlap"
        " WHERE build_run_id = %s"
        " AND (person_a_id = %s OR person_b_id = %s)"
        " ORDER BY person_a_id, person_b_id, canonical_company_id,"
        " role_context, co_participation_class",
        (build_run_id, person_id, person_id)).fetchall()


def pair_rows_between(conn, build_run_id, person_a_id, person_b_id):
    return conn.execute(
        f"SELECT {_PPO_COLS} FROM {SCHEMA}.person_person_overlap"
        " WHERE build_run_id = %s AND person_a_id = %s AND person_b_id = %s"
        " ORDER BY canonical_company_id, role_context, co_participation_class",
        (build_run_id, min(person_a_id, person_b_id),
         max(person_a_id, person_b_id))).fetchall()


def observations_for_pairs(conn, build_run_id, pair_companies):
    """Batched contributing observations for explicit (company, person) cells.

    pair_companies: iterable of (person_id, company_id) — one query total.
    """
    cells = list({(int(p), int(c)) for p, c in pair_companies})
    if not cells:
        return []
    return conn.execute(
        f"SELECT {_OBS_COLS} FROM {SCHEMA}.person_company_observation o"
        " WHERE o.build_run_id = %s AND (o.canonical_person_id,"
        " o.canonical_company_id) IN (SELECT * FROM unnest(%s::bigint[],"
        " %s::bigint[])) ORDER BY o.observed_at NULLS LAST, o.observation_id",
        (build_run_id, [p for p, _ in cells], [c for _, c in cells])).fetchall()


def current_authoritative_observations(conn, build_run_id, person_ids):
    """AUTHORITATIVE_CURRENT_GOVERNANCE observations for persons — the only
    evidence the UI may present as current board/management."""
    ids = sorted({int(p) for p in person_ids})
    if not ids:
        return []
    return conn.execute(
        f"SELECT {_OBS_COLS} FROM {SCHEMA}.person_company_observation"
        " WHERE build_run_id = %s AND canonical_person_id = ANY(%s)"
        " AND authority_class = 'AUTHORITATIVE_CURRENT_GOVERNANCE'"
        " ORDER BY canonical_person_id, canonical_company_id",
        (build_run_id, ids)).fetchall()


# --- periods -----------------------------------------------------------------

def periods_for_person(conn, build_run_id, person_id):
    return conn.execute(
        f"SELECT {_PERIOD_COLS} FROM {SCHEMA}.person_company_period"
        " WHERE build_run_id = %s AND canonical_person_id = %s"
        " ORDER BY role_context, period_start NULLS LAST, canonical_company_id",
        (build_run_id, person_id)).fetchall()


def periods_for_company(conn, build_run_id, company_id):
    return conn.execute(
        f"SELECT {_PERIOD_COLS} FROM {SCHEMA}.person_company_period"
        " WHERE build_run_id = %s AND canonical_company_id = %s"
        " ORDER BY role_context, period_start NULLS LAST, canonical_person_id",
        (build_run_id, company_id)).fetchall()


def period_counts_for_company(conn, build_run_id, company_ids):
    """Batched period counts per (company, person, context) — for company
    people rendering without per-person queries."""
    ids = sorted({int(c) for c in company_ids})
    if not ids:
        return []
    return conn.execute(
        f"SELECT canonical_company_id, canonical_person_id, role_context,"
        " count(*) AS period_count, min(period_start) AS first_start,"
        " max(period_end) AS last_end"
        f" FROM {SCHEMA}.person_company_period"
        " WHERE build_run_id = %s AND canonical_company_id = ANY(%s)"
        " GROUP BY 1, 2, 3",
        (build_run_id, ids)).fetchall()


# --- groups ------------------------------------------------------------------

def groups_for_person(conn, build_run_id, person_id):
    return conn.execute(
        f"SELECT g.group_id, g.build_run_id, g.member_set_hash, g.role_context,"
        " g.group_kind, g.group_size, g.support, g.first_observed, g.last_observed"
        f" FROM {SCHEMA}.person_group g"
        f" JOIN {SCHEMA}.person_group_member m ON m.group_id = g.group_id"
        " WHERE g.build_run_id = %s AND m.canonical_person_id = %s"
        " ORDER BY g.group_kind, g.role_context, g.group_size DESC, g.group_id",
        (build_run_id, person_id)).fetchall()


def groups_for_company(conn, build_run_id, company_id):
    return conn.execute(
        f"SELECT DISTINCT g.group_id, g.build_run_id, g.member_set_hash,"
        " g.role_context, g.group_kind, g.group_size, g.support,"
        " g.first_observed, g.last_observed"
        f" FROM {SCHEMA}.person_group g"
        f" JOIN {SCHEMA}.person_group_observation o"
        "  ON o.group_id = g.group_id AND o.build_run_id = g.build_run_id"
        " WHERE g.build_run_id = %s AND o.canonical_company_id = %s"
        " ORDER BY g.group_kind, g.role_context, g.group_size DESC, g.group_id",
        (build_run_id, company_id)).fetchall()


def group_members_bulk(conn, build_run_id, group_ids):
    """Batched members for many groups: one query, grouped client-side."""
    ids = sorted({int(g) for g in group_ids})
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT m.group_id, m.canonical_person_id, ce.display_name"
        f" FROM {SCHEMA}.person_group_member m"
        " JOIN canonical_entity ce ON ce.canonical_entity_id ="
        "  m.canonical_person_id"
        " WHERE m.group_id = ANY(%s)"
        " ORDER BY ce.display_name, m.canonical_person_id",
        (ids,)).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["group_id"], []).append(
            {"canonical_person_id": r["canonical_person_id"],
             "display_name": r["display_name"]})
    return out


def group_observations(conn, build_run_id, group_ids):
    ids = sorted({int(g) for g in group_ids})
    if not ids:
        return []
    return conn.execute(
        "SELECT go.group_observation_id, go.group_id, go.canonical_company_id,"
        " go.role_context, go.episode_key, go.episode_start, go.episode_end,"
        " go.source_family, go.co_observation_kind"
        f" FROM {SCHEMA}.person_group_observation go"
        " WHERE go.build_run_id = %s AND go.group_id = ANY(%s)"
        " ORDER BY go.group_id, go.episode_start NULLS LAST,"
        " go.episode_key",
        (build_run_id, ids)).fetchall()


def group_observation_members(conn, build_run_id, observation_ids):
    """Junction members for group observations — batched provenance-ready."""
    ids = sorted({int(o) for o in observation_ids})
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT j.group_observation_id, j.observation_id, j.role_in_episode,"
        f"       o.canonical_person_id, ce.display_name"
        f" FROM {SCHEMA}.person_group_observation_member j"
        f" JOIN {SCHEMA}.person_company_observation o"
        "  ON o.observation_id = j.observation_id"
        " JOIN canonical_entity ce ON ce.canonical_entity_id ="
        "  o.canonical_person_id"
        " WHERE j.group_observation_id = ANY(%s)"
        " ORDER BY ce.display_name, o.canonical_person_id",
        (ids,)).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["group_observation_id"], []).append(r)
    return out


def group_observation_sources(conn, build_run_id, observation_ids):
    """Source references for the junction observations of group episodes."""
    ids = sorted({int(o) for o in observation_ids})
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT o.observation_id, o.source_document_id, o.source_event_id,"
        " o.source_history_event_id, o.source_relation_id, o.source_attempt_id,"
        " o.source_projection_run_id, o.source_family, o.authority_class,"
        " o.observed_at, o.action"
        f" FROM {SCHEMA}.person_company_observation o"
        " WHERE o.observation_id = ANY(%s)", (ids,)).fetchall()
    return {r["observation_id"]: r for r in rows}


def group_by_id(conn, build_run_id, group_id):
    return conn.execute(
        f"SELECT {_GROUP_COLS} FROM {SCHEMA}.person_group"
        " WHERE build_run_id = %s AND group_id = %s",
        (build_run_id, group_id)).fetchone()


# --- company people ----------------------------------------------------------

def observations_for_company(conn, build_run_id, company_id):
    return conn.execute(
        f"SELECT {_OBS_COLS} FROM {SCHEMA}.person_company_observation"
        " WHERE build_run_id = %s AND canonical_company_id = %s"
        " ORDER BY observed_at NULLS LAST, observation_id",
        (build_run_id, company_id)).fetchall()


def observations_for_companies(conn, build_run_id, company_ids):
    ids = sorted({int(c) for c in company_ids})
    if not ids:
        return []
    return conn.execute(
        f"SELECT {_OBS_COLS} FROM {SCHEMA}.person_company_observation"
        " WHERE build_run_id = %s AND canonical_company_id = ANY(%s)"
        " ORDER BY canonical_company_id, observed_at NULLS LAST,"
        " observation_id",
        (build_run_id, ids)).fetchall()


# --- names -------------------------------------------------------------------

def names_for(conn, canonical_entity_ids):
    """Batched display names; canonical ids only (click-through contract)."""
    ids = sorted({int(i) for i in canonical_entity_ids if i is not None})
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT canonical_entity_id, display_name, entity_kind"
        " FROM canonical_entity WHERE canonical_entity_id = ANY(%s)",
        (ids,)).fetchall()
    return {r["canonical_entity_id"]: r for r in rows}