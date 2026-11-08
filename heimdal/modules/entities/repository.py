# heimdal/modules/entities/repository.py
"""SQL queries for entity browsing — all read from schema_read.sql views."""
from heimdal.config import _DIRECT_COLS
from heimdal.modules.shared.db import either_side


def select_owners_of(conn, canonical_entity_id):
    """§16.1: rows where this entity is the OWNED side. Incoming only."""
    return conn.execute(
        f"SELECT {_DIRECT_COLS} FROM v_direct_ownership WHERE owned_id = %s"
        " ORDER BY parsed_percentage DESC NULLS LAST, owner_name",
        (canonical_entity_id,)).fetchall()


def select_holdings_of(conn, canonical_entity_id):
    """§16.2: rows where this entity is the OWNER side. Outgoing only."""
    return conn.execute(
        f"SELECT {_DIRECT_COLS} FROM v_direct_ownership WHERE owner_id = %s"
        " ORDER BY parsed_percentage DESC NULLS LAST, owned_name",
        (canonical_entity_id,)).fetchall()


# --- control registration (§16.1a / §17.1a) -----------------------------------

def select_no_actor_status(conn, source_entity_id):
    """§14a.2 status row: a source's explicit "no registered control actor" claim."""
    return conn.execute(
        "SELECT raw_source_label FROM entity_status"
        " WHERE source_entity_id = %s AND canonical_status_type"
        " = 'NO_REGISTERED_CONTROL_ACTOR'", (source_entity_id,)).fetchone()


def select_control_registration(conn, canonical_entity_id, source_entity_id):
    """§16.1a: registered_id literal — rows where this entity is registered_id."""
    return conn.execute(
        "SELECT relation_id, actor_id, actor_name, actor_kind,"
        " raw_source_relation_label, semantic_direction_confidence, source_attempt_id"
        " FROM v_control_registration WHERE registered_id = %s ORDER BY actor_name",
        (canonical_entity_id,)).fetchall()


def select_control_registrations_of_actor(conn, canonical_entity_id, source_entity_id):
    """§17.1a: actor_id literal — rows where this entity is actor_id."""
    return conn.execute(
        "SELECT relation_id, registered_id, registered_name,"
        " raw_source_relation_label, semantic_direction_confidence, source_attempt_id"
        " FROM v_control_registration WHERE actor_id = %s ORDER BY registered_name",
        (canonical_entity_id,)).fetchall()


# --- roles (§16.3 / §17.3) ----------------------------------------------------

def select_roles_at_company(conn, canonical_entity_id):
    """§16.3: Nevnd/Stjórn/Annað relations for this company (never EIGARAR)."""
    return conn.execute(
        "SELECT relation_id, person_id, person_name, inferred_group,"
        " inferred_group_confidence, canonical_role_label, raw_source_relation_label,"
        " semantic_direction_confidence"
        " FROM v_role_relation WHERE company_id = %s AND inferred_group != 'EIGARAR'"
        " ORDER BY inferred_group, person_name",
        (canonical_entity_id,)).fetchall()


def select_voting_rights_at_company(conn, canonical_entity_id):
    """Atkvøðurættur (voting rights, cbTypeId 101) at this company."""
    return conn.execute(
        "SELECT relation_id, person_id, person_name, canonical_role_label,"
        " raw_source_relation_label, semantic_direction_confidence"
        " FROM v_role_relation WHERE company_id = %s AND inferred_group = 'EIGARAR'"
        " ORDER BY person_name",
        (canonical_entity_id,)).fetchall()


def select_roles_held_by_person(conn, canonical_entity_id):
    """§17.3: Nevnd/Stjórn/Annað relations this person holds (never EIGARAR)."""
    return conn.execute(
        "SELECT relation_id, company_id, company_name, inferred_group,"
        " inferred_group_confidence, canonical_role_label, raw_source_relation_label,"
        " semantic_direction_confidence"
        " FROM v_role_relation WHERE person_id = %s AND inferred_group != 'EIGARAR'"
        " ORDER BY inferred_group, company_name",
        (canonical_entity_id,)).fetchall()


def select_voting_rights_held_by_person(conn, canonical_entity_id):
    """Atkvøðurættur (voting rights, cbTypeId 101) this person holds."""
    return conn.execute(
        "SELECT relation_id, company_id, company_name, canonical_role_label,"
        " raw_source_relation_label, semantic_direction_confidence"
        " FROM v_role_relation WHERE person_id = %s AND inferred_group = 'EIGARAR'"
        " ORDER BY company_name",
        (canonical_entity_id,)).fetchall()


# --- indirect ownership (§15 / §16.1 / §16.2) --------------------------------
# registry_ownership.indirect_ownership and registry_ownership.ownership_cycle_member still key on the raw
# source_entity_id, not on canonical_entity_id — resolve before querying.

def select_cycle_info(conn, source_entity_id):
    """None, or the cycle this entity sits in. SoT §15: a cycle member never
    gets a computed indirect percentage — the cycle is explained instead."""
    row = conn.execute(
        "SELECT cycle_group_id FROM registry_ownership.ownership_cycle_member WHERE entity_id = %s",
        (source_entity_id,)).fetchone()
    if row is None:
        return None
    members = conn.execute(
        "SELECT ce.canonical_entity_id, ce.display_name, ce.entity_kind"
        " FROM registry_ownership.ownership_cycle_member m"
        " JOIN canonical_entity ce ON ce.source_entity_id = m.entity_id"
        " WHERE m.cycle_group_id = %s ORDER BY ce.display_name",
        (row["cycle_group_id"],)).fetchall()
    ids = [m["canonical_entity_id"] for m in members]
    edges = conn.execute(
        f"SELECT {_DIRECT_COLS} FROM v_direct_ownership"
        " WHERE owner_id = ANY(%s) AND owned_id = ANY(%s)"
        " ORDER BY owner_name, owned_name", (ids, ids)).fetchall()
    return {"cycle_group_id": row["cycle_group_id"], "members": members,
            "edges": edges, "self_loop": len(members) == 1}


def select_indirect_owners_of(conn, source_entity_id):
    """§16.1 indirect side. owned_entity_id literal — incoming only."""
    return conn.execute(
        "SELECT owner_entity_id AS counterparty_source_id, computed_percentage,"
        " path_count, contributing_paths, paths_truncated"
        " FROM registry_ownership.indirect_ownership WHERE owned_entity_id = %s"
        " ORDER BY computed_percentage DESC", (source_entity_id,)).fetchall()


def select_indirect_holdings_of(conn, source_entity_id):
    """§16.2 indirect side. owner_entity_id literal — outgoing only."""
    return conn.execute(
        "SELECT owned_entity_id AS counterparty_source_id, computed_percentage,"
        " path_count, contributing_paths, paths_truncated"
        " FROM registry_ownership.indirect_ownership WHERE owner_entity_id = %s"
        " ORDER BY computed_percentage DESC", (source_entity_id,)).fetchall()


def select_cycle_passing(conn, source_entity_id, incoming):
    """C-3 (owner D8 2026-11-06): counterparties whose ownership chain to
    (incoming) or from this entity runs over a cycle edge — an edge whose two
    endpoints sit in the same ownership_cycle_member group. compute_indirect_
    ownership drops those edges (§15), so that chain's share never reaches
    indirect_ownership. Read-time walk over simple paths only (no entity is
    revisited, so it ends on cycles; self-edges never extend a path). The
    entity's own cycle-mates are left out: the cycle explainer covers them.
    Measured on the 2026-09-04 data: 270 pairs, ≤ 20 ms per entity."""
    near, far = ("object", "subject") if incoming else ("subject", "object")
    return conn.execute(
        "WITH RECURSIVE walk(node, path, groups) AS ("
        "  SELECT %(x)s::bigint, ARRAY[%(x)s::bigint], ARRAY[]::int[]"
        "  UNION ALL"
        f" SELECT r.canonical_{far}_entity_id, w.path || r.canonical_{far}_entity_id,"
        "   w.groups || CASE WHEN a.cycle_group_id = b.cycle_group_id"
        "                    THEN ARRAY[a.cycle_group_id] ELSE ARRAY[]::int[] END"
        "  FROM walk w"
        "  JOIN registry_ownership.relation r"
        f"   ON r.canonical_{near}_entity_id = w.node"
        "   AND r.canonical_relation_type = 'DIRECT_OWNERSHIP'"
        "  LEFT JOIN registry_ownership.ownership_cycle_member a"
        "   ON a.entity_id = r.canonical_subject_entity_id"
        "  LEFT JOIN registry_ownership.ownership_cycle_member b"
        "   ON b.entity_id = r.canonical_object_entity_id"
        f" WHERE r.canonical_{far}_entity_id <> ALL(w.path))"
        " SELECT w.node AS counterparty_source_id, ce.canonical_entity_id,"
        "  ce.display_name, ce.entity_kind,"
        "  array_agg(DISTINCT g ORDER BY g) AS cycle_group_ids"
        " FROM walk w CROSS JOIN LATERAL unnest(w.groups) g"
        " LEFT JOIN canonical_entity ce ON ce.source_entity_id = w.node"
        " WHERE NOT EXISTS (SELECT 1 FROM registry_ownership.ownership_cycle_member m1"
        "  JOIN registry_ownership.ownership_cycle_member m2 USING (cycle_group_id)"
        "  WHERE m1.entity_id = w.node AND m2.entity_id = %(x)s)"
        " GROUP BY w.node, ce.canonical_entity_id, ce.display_name, ce.entity_kind"
        " ORDER BY ce.display_name, w.node",
        {"x": source_entity_id}).fetchall()


def select_hydrate_paths_nodes(conn, source_ids):
    """canonical_entity rows behind contributing_paths: {source_entity_id: row}."""
    return {n["source_entity_id"]: n for n in conn.execute(
        "SELECT source_entity_id, canonical_entity_id, display_name, entity_kind"
        " FROM canonical_entity WHERE source_entity_id = ANY(%s)",
        (list(source_ids),)).fetchall()}


def select_hydrate_paths_edges(conn, canonical_ids):
    """direct ownership edges within the node set: {(owner_id, owned_id): row}."""
    edges = {}
    for e in conn.execute(
            f"SELECT {_DIRECT_COLS} FROM v_direct_ownership"
            " WHERE owner_id = ANY(%s) AND owned_id = ANY(%s)",
            (canonical_ids, canonical_ids)).fetchall():
        edges.setdefault((e["owner_id"], e["owned_id"]), e)
    return edges


# --- oversigt + lister (§16 forside / §16 / §17) -----------------------------

def select_overview(conn, unresolved):
    """Rene optællinger af det, indsamleren allerede har skrevet. Ingen ny
    udledning, ingen upstream-kald. Porten læser med heimdal_reader og har
    med vilje ingen adgang til enumeration_key, så nøglerumsfejningen kan
    ikke opgøres her."""
    row = conn.execute(
        "SELECT (SELECT count(*) FROM company_inventory) AS inventory,"
        " (SELECT count(*) FROM company_inventory WHERE inventory_status = ANY(%s))"
        "   AS unresolved,"
        " (SELECT count(*) FROM registry_ownership.indirect_ownership) AS indirect,"
        " (SELECT count(DISTINCT cycle_group_id) FROM registry_ownership.ownership_cycle_member) AS cycle_groups,"
        " (SELECT count(DISTINCT entity_id) FROM registry_ownership.ownership_cycle_member) AS cycle_entities,"
        " (SELECT count(*) FROM entity_status) AS no_actor_status",
        (unresolved,)).fetchone()
    row["kinds"] = conn.execute(
        "SELECT entity_kind, count(*) AS n FROM canonical_entity"
        " GROUP BY 1 ORDER BY 2 DESC").fetchall()
    row["relations"] = conn.execute(
        "SELECT canonical_relation_type AS t, count(*) AS n FROM registry_ownership.relation"
        " GROUP BY 1 ORDER BY 2 DESC").fetchall()
    row["inventory_states"] = conn.execute(
        "SELECT inventory_status AS s, count(*) AS n FROM company_inventory"
        " GROUP BY 1 ORDER BY 2 DESC").fetchall()
    row["freshness"] = conn.execute(
        "SELECT count(*) AS n, min(last_fetched_at) AS oldest, max(last_fetched_at) AS newest"
        " FROM v_provenance").fetchone()
    row["runs"] = conn.execute(
        "SELECT collection_run_id, run_type, run_status, stop_reason, ended_at"
        " FROM collection_run ORDER BY collection_run_id DESC LIMIT 5").fetchall()
    return row


def select_entity_list(conn, kinds, query, offset, per_page, sort, ownership_band, kind, norm_like):
    """§22: query matches either display_name or registration_number with Faroese normalization."""
    if kind and kind in ("COMPANY", "ORGANIZATION", "PUBLIC_AUTHORITY", "UNKNOWN_ENTITY", "PERSON"):
        kinds = [kind]
    else:
        kinds = list(kinds)
    desc = sort == "-name"
    direction = "DESC" if desc else "ASC"
    like = f"%{query}%" if query else "%"
    normalized = query.lstrip("Ss#").strip() if query else ""
    regnr_like = f"%{normalized}%" if normalized else None

    where_parts = [
        "entity_kind = ANY(%s)",
        "(display_name ILIKE %s OR translate(lower(display_name), 'áðíóúýæøö', 'adiouyaeoo') ILIKE %s OR registration_number ILIKE %s)"
    ]
    params = [kinds, like, norm_like, regnr_like]

    if ownership_band:
        band_sql = {
            "100": "EXISTS (SELECT 1 FROM v_direct_ownership vdo WHERE vdo.owned_id = v_entity.canonical_entity_id AND vdo.parsed_percentage = 100.0)",
            "50-99": "EXISTS (SELECT 1 FROM v_direct_ownership vdo WHERE vdo.owned_id = v_entity.canonical_entity_id AND vdo.parsed_percentage >= 50.0 AND vdo.parsed_percentage < 100.0)",
            "25-49": "EXISTS (SELECT 1 FROM v_direct_ownership vdo WHERE vdo.owned_id = v_entity.canonical_entity_id AND vdo.parsed_percentage >= 25.0 AND vdo.parsed_percentage < 50.0)",
            "10-24": "EXISTS (SELECT 1 FROM v_direct_ownership vdo WHERE vdo.owned_id = v_entity.canonical_entity_id AND vdo.parsed_percentage >= 10.0 AND vdo.parsed_percentage < 25.0)",
            "1-9": "EXISTS (SELECT 1 FROM v_direct_ownership vdo WHERE vdo.owned_id = v_entity.canonical_entity_id AND vdo.parsed_percentage >= 1.0 AND vdo.parsed_percentage < 10.0)"
        }.get(ownership_band)
        if band_sql:
            where_parts.append(band_sql)

    where = " AND ".join(where_parts)
    total = conn.execute(
        f"SELECT count(*) AS n FROM v_entity WHERE {where}",
        params).fetchone()["n"]
    pages = max(1, -(-total // per_page))
    offset = min(offset, (pages - 1) * per_page)
    query_params = list(params) + [per_page, offset]
    rows = conn.execute(
        f"SELECT canonical_entity_id, source_entity_id, entity_kind, display_name,"
        f" registration_number, inventory_status FROM v_entity WHERE {where}"
        f" ORDER BY display_name {direction}, canonical_entity_id {direction}"
        " LIMIT %s OFFSET %s",
        query_params).fetchall()
    return {"rows": rows, "total": total, "page": (offset // per_page) + 1, "pages": pages}


def select_person_list(conn, query, offset, per_page, key, desc, norm_like):
    """§17: person registry with Faroese fuzzy search."""
    direction = "DESC" if desc else "ASC"
    like = f"%{query}%" if query else "%"
    order = {
        "name": f"ve.display_name {direction}, ve.canonical_entity_id {direction}",
        "id": f"ve.canonical_entity_id {direction}",
        "direct": f"COALESCE(d.n, 0) {direction}, ve.display_name ASC, ve.canonical_entity_id ASC",
        "indirect": f"COALESCE(i.n, 0) {direction}, ve.display_name ASC, ve.canonical_entity_id ASC",
        "combined": f"COALESCE(c.n, 0) {direction}, ve.display_name ASC, ve.canonical_entity_id ASC",
        "nevn": f"COALESCE(v.n, 0) {direction}, ve.display_name ASC, ve.canonical_entity_id ASC",
        "stjorn": f"COALESCE(st.n, 0) {direction}, ve.display_name ASC, ve.canonical_entity_id ASC",
    }[key]
    total = conn.execute(
        "SELECT count(*) AS n FROM v_entity ve"
        " WHERE ve.entity_kind = 'PERSON' AND (ve.display_name ILIKE %s OR translate(lower(ve.display_name), 'áðíóúýæøö', 'adiouyaeoo') ILIKE %s)",
        (like, norm_like)).fetchone()["n"]
    pages = max(1, -(-total // per_page))
    offset = min(offset, (pages - 1) * per_page)  # clamp a huge ?page= to the last page, not a huge OFFSET
    rows = conn.execute(
        "WITH direct AS ("
        "  SELECT owner_id AS pid, count(DISTINCT owned_id) AS n FROM v_direct_ownership"
        "  WHERE owner_kind = 'PERSON' AND owned_kind <> 'PERSON' GROUP BY owner_id),"
        " indirect AS ("
        "  SELECT o.canonical_entity_id AS pid, count(DISTINCT d.canonical_entity_id) AS n"
        "  FROM registry_ownership.indirect_ownership io"
        "  JOIN canonical_entity o ON o.source_entity_id = io.owner_entity_id AND o.entity_kind = 'PERSON'"
        "  JOIN canonical_entity d ON d.source_entity_id = io.owned_entity_id AND d.entity_kind <> 'PERSON'"
        "  WHERE io.owner_entity_id <> io.owned_entity_id GROUP BY o.canonical_entity_id),"
        " combined AS ("
        "  SELECT pid, count(*) AS n FROM ("
        "    SELECT owner_id AS pid, owned_id AS cid FROM v_direct_ownership"
        "    WHERE owner_kind = 'PERSON' AND owned_kind <> 'PERSON'"
        "    UNION"
        "    SELECT o.canonical_entity_id, d.canonical_entity_id"
        "    FROM registry_ownership.indirect_ownership io"
        "    JOIN canonical_entity o ON o.source_entity_id = io.owner_entity_id AND o.entity_kind = 'PERSON'"
        "    JOIN canonical_entity d ON d.source_entity_id = io.owned_entity_id AND d.entity_kind <> 'PERSON'"
        "    WHERE io.owner_entity_id <> io.owned_entity_id) u GROUP BY pid),"
        " nevnd AS ("
        "  SELECT person_id AS pid, count(DISTINCT company_id) AS n FROM v_role_relation"
        "  WHERE inferred_group = 'NEVND' GROUP BY person_id),"
        " stjorn AS ("
        "  SELECT person_id AS pid, count(DISTINCT company_id) AS n FROM v_role_relation"
        "  WHERE inferred_group = 'STJORN' GROUP BY person_id)"
        " SELECT ve.canonical_entity_id, ve.source_entity_id, ve.entity_kind, ve.display_name,"
        " ve.registration_number, ve.inventory_status,"
        " COALESCE(d.n, 0) AS direct_count, COALESCE(i.n, 0) AS indirect_count,"
        " COALESCE(c.n, 0) AS combined_count, COALESCE(v.n, 0) AS nevn_count,"
        " COALESCE(st.n, 0) AS stjorn_count"
        " FROM v_entity ve"
        " LEFT JOIN direct d ON d.pid = ve.canonical_entity_id"
        " LEFT JOIN indirect i ON i.pid = ve.canonical_entity_id"
        " LEFT JOIN combined c ON c.pid = ve.canonical_entity_id"
        " LEFT JOIN nevnd v ON v.pid = ve.canonical_entity_id"
        " LEFT JOIN stjorn st ON st.pid = ve.canonical_entity_id"
        " WHERE ve.entity_kind = 'PERSON' AND (ve.display_name ILIKE %s OR translate(lower(ve.display_name), 'áðíóúýæøö', 'adiouyaeoo') ILIKE %s)"
        + f" ORDER BY {order} LIMIT %s OFFSET %s",
        (like, norm_like, per_page, offset)).fetchall()
    return {"rows": rows, "total": total, "page": (offset // per_page) + 1, "pages": pages}


# --- §21 netværk (ego graph, depth 1) ----------------------------------------

def select_ego_graph(conn, canonical_entity_id, source_entity_id):
    """Raw rows from ego_graph's three edge views — ownership, control
    registration, roles — each converted to the same {from_id, to_id, kind,
    label} shape in service.ego_graph so the template renders one edge list."""
    one = {"id": canonical_entity_id}
    return {
        "direct": conn.execute(
            either_side(_DIRECT_COLS, "v_direct_ownership", "owner_id", "owned_id"), one).fetchall(),
        "control": conn.execute(
            either_side("relation_id, actor_id, actor_name, actor_kind, registered_id, registered_name",
                        "v_control_registration", "actor_id", "registered_id"), one).fetchall(),
        "roles": conn.execute(
            either_side("person_id, person_name, company_id, company_name, canonical_role_label",
                        "v_role_relation", "person_id", "company_id"), one).fetchall(),
    }


# --- relationer (§16.4/§17.4/§18/§19) + PEP -----------------------------------
# company_a/b_entity_id, person_a/b_entity_id and related_entity_id on the
# source tables are raw source_entity_id values — resolved to canonical ids
# here so nothing downstream ever sees a raw id.

def select_company_relations_of(conn, source_entity_id):
    """§16.4/§19: registry_ownership.company_relation_context rows this company appears in,
    with the shared entity and both sides resolved."""
    return conn.execute(
        "SELECT crc.relation_basis, crc.related_entity_id, crc.company_a_relation,"
        " crc.company_b_relation, crc.company_a_entity_id, crc.company_b_entity_id,"
        " ca.canonical_entity_id AS a_id, ca.display_name AS a_name, ca.entity_kind AS a_kind,"
        " cb.canonical_entity_id AS b_id, cb.display_name AS b_name, cb.entity_kind AS b_kind,"
        " cr.canonical_entity_id AS related_id, cr.display_name AS related_name"
        " FROM registry_ownership.company_relation_context crc"
        " JOIN canonical_entity ca ON ca.source_entity_id = crc.company_a_entity_id"
        " JOIN canonical_entity cb ON cb.source_entity_id = crc.company_b_entity_id"
        " LEFT JOIN canonical_entity cr ON cr.source_entity_id = crc.related_entity_id"
        " WHERE crc.company_a_entity_id = %s OR crc.company_b_entity_id = %s",
        (source_entity_id, source_entity_id)).fetchall()


def select_person_relations_of(conn, source_entity_id):
    """§17.4/§18: registry_ownership.person_relation_context rows this person appears in,
    with the shared company and both sides resolved."""
    return conn.execute(
        "SELECT prc.person_a_entity_id, prc.person_b_entity_id, prc.company_entity_id,"
        " ra.canonical_relation_type AS type_a, rb.canonical_relation_type AS type_b,"
        " prc.person_a_group, prc.person_b_group,"
        " prc.person_a_role_label, prc.person_b_role_label,"
        " ca.canonical_entity_id AS a_id, ca.display_name AS a_name,"
        " cb.canonical_entity_id AS b_id, cb.display_name AS b_name,"
        " cc.canonical_entity_id AS company_id, cc.display_name AS company_name"
        " FROM registry_ownership.person_relation_context prc"
        " JOIN registry_ownership.relation ra ON ra.relation_id = prc.person_a_relation_id"
        " JOIN registry_ownership.relation rb ON rb.relation_id = prc.person_b_relation_id"
        " JOIN canonical_entity ca ON ca.source_entity_id = prc.person_a_entity_id"
        " JOIN canonical_entity cb ON cb.source_entity_id = prc.person_b_entity_id"
        " JOIN canonical_entity cc ON cc.source_entity_id = prc.company_entity_id"
        " WHERE prc.person_a_entity_id = %s OR prc.person_b_entity_id = %s",
        (source_entity_id, source_entity_id)).fetchall()


def select_person_pep_info(conn, canonical_entity_id):
    """PEP details for a person if applicable."""
    return conn.execute(
        "SELECT * FROM v_pep_actors WHERE canonical_person_id = %s",
        (canonical_entity_id,)
    ).fetchall()
