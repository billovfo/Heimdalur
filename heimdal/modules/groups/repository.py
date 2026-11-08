# heimdal/modules/groups/repository.py
"""SQL queries for corporate groups — all read from schema_read.sql views."""

# Self-edges (C-8; owner D8 2026-11-06). The source registers 70 DIRECT_OWNERSHIP
# self-edges — a company recorded as owning part of itself — 6 of them above
# 50 % (not the 5 the older docs and tests name). Rule: a self-edge is a §15
# cycle of one member. Its direct row is shown verbatim (company page, ego
# graph), the cycle explainer says "ejer en andel af sig selv", and it never
# yields an indirect or derived percentage. compute_indirect_ownership already
# keeps it out of indirect_ownership (a singleton cycle group), the ejertræ
# never draws it, and the C-3 cycle walk never extends a path over it. The
# group queries below derive parents, subsidiaries and cash-flow % from direct
# edges, so every one reads through _EDGES: a self-edge never makes a company
# its own parent or subsidiary, and never feeds a cash-flow %.
_EDGES = "(SELECT * FROM v_direct_ownership WHERE owner_id <> owned_id) d"


def select_majority_parent(conn, curr_id):
    # Only corporate entities (COMPANY, ORGANIZATION, PUBLIC_AUTHORITY) can be
    # majority parents. A PERSON owning >50% is a direct owner (shown in §16.1)
    # but is never followed up the corporate hierarchy — a person is not a
    # parent company. See post-review fix 2026-08-18.
    return conn.execute(f"""
        SELECT owner_id, owner_name, owner_kind, parsed_percentage
        FROM {_EDGES}
        WHERE owned_id = %s AND parsed_percentage > 50.0
              AND owner_kind IN ('COMPANY', 'ORGANIZATION', 'PUBLIC_AUTHORITY')
        LIMIT 1
    """, (curr_id,)).fetchone()


def has_majority_owned(conn, canonical_entity_id):
    return conn.execute(
        f"SELECT 1 FROM {_EDGES} WHERE owner_id = %s AND parsed_percentage > 50.0 LIMIT 1",
        (canonical_entity_id,)).fetchone() is not None


def select_subsidiaries(conn, pid):
    return conn.execute(f"""
        SELECT owned_id, owned_name, owned_kind, parsed_percentage
        FROM {_EDGES}
        WHERE owner_id = %s AND parsed_percentage > 50.0
        ORDER BY parsed_percentage DESC, owned_name
    """, (pid,)).fetchall()


def select_associated(conn, pid):
    return conn.execute(f"""
        SELECT owned_id, owned_name, owned_kind, parsed_percentage
        FROM {_EDGES}
        WHERE owner_id = %s AND parsed_percentage >= 20.0 AND parsed_percentage <= 50.0
        ORDER BY parsed_percentage DESC, owned_name
    """, (pid,)).fetchall()


def _corporate_groups_sql(q=None):
    """T5.3 (F-16): shared read-only CTE; optional q narrows on the parent's
    registered name or registration number (no matching semantics change)."""
    params = []
    where = ""
    if q:
        like = "%" + q + "%"
        where = (" WHERE (ce.display_name ILIKE %s"
                 " OR ce.registration_number ILIKE %s)")
        params = [like, like]
    base = (
        "\n        WITH RECURSIVE majority_edges AS (\n"
        "            SELECT owner_id, owned_id\n"
        f"            FROM {_EDGES}\n"
        "            WHERE parsed_percentage > 50.0\n"
        "                  AND owner_kind IN ('COMPANY', 'ORGANIZATION', 'PUBLIC_AUTHORITY')\n"
        "        ),\n"
        "        top_parents AS (\n"
        "            SELECT DISTINCT m.owner_id AS parent_id\n"
        "            FROM majority_edges m\n"
        "            WHERE NOT EXISTS (\n"
        "                SELECT 1 FROM majority_edges sub WHERE sub.owned_id = m.owner_id\n"
        "            )\n"
        "        ),\n"
        "        matched_parents AS (\n"
        "            SELECT tp.parent_id\n"
        "            FROM top_parents tp\n"
        "            JOIN v_entity ce ON ce.canonical_entity_id = tp.parent_id"
        + where +
        "\n        )\n"
        "    ")
    return base, params


def select_all_corporate_groups(conn, limit, offset, q=None):
    # Same filter as select_majority_parent: only corporate parents count.
    # PERSON owners are direct owners (§16.1) but never corporate parents.
    # T5.3: optional q (registered name / registration number) + offset.
    base, params0 = _corporate_groups_sql(q)
    sql = base + """
        SELECT
            mp.parent_id,
            ce.display_name AS parent_name,
            ce.entity_kind AS parent_kind,
            ce.registration_number AS parent_regnr,
            count(DISTINCT me.owned_id) AS subsidiary_count
        FROM matched_parents mp
        JOIN v_entity ce ON ce.canonical_entity_id = mp.parent_id
        JOIN majority_edges me ON me.owner_id = mp.parent_id
        GROUP BY mp.parent_id, ce.display_name, ce.entity_kind, ce.registration_number
        ORDER BY subsidiary_count DESC, parent_name
        LIMIT %s OFFSET %s
    """
    return conn.execute(sql, params0 + [limit, offset]).fetchall()


def count_all_corporate_groups(conn, q=None):
    """T5.3: total group count (same CTE, read-only) for the pager."""
    base, params0 = _corporate_groups_sql(q)
    sql = base + """
        SELECT count(*) AS n FROM matched_parents
    """
    return conn.execute(sql, params0).fetchone()
