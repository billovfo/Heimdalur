# heimdal/modules/compliance/repository.py
"""SQL for the neutral register observations — all read from schema_read.sql views."""
from heimdal.modules.shared.inputs import clamp_offset


def structure_observation_summary(conn):
    row = conn.execute("""
    SELECT
        count(DISTINCT company_id) AS total_observed_companies,
        count(*) FILTER (WHERE observation_type = 'OWNERSHIP_CYCLE') AS ownership_cycle_count,
        count(*) FILTER (WHERE observation_type = 'SAME_SURNAME_SHARE') AS same_surname_share_count
    FROM v_structure_observations
""").fetchone()
    return dict(row) if row else {
        "total_observed_companies": 0,
        "ownership_cycle_count": 0,
        "same_surname_share_count": 0,
    }


def registration_patterns(conn, entity_id=None, limit=50):
    if entity_id:
        return conn.execute("""
        SELECT entity_id, entity_name, entity_kind, pattern_type, pattern_label, observed_value
        FROM v_registration_patterns
        WHERE entity_id = %s
        ORDER BY entity_name, pattern_type
        LIMIT %s
    """, (entity_id, limit)).fetchall()
    else:
        return conn.execute("""
        SELECT entity_id, entity_name, entity_kind, pattern_type, pattern_label, observed_value
        FROM v_registration_patterns
        ORDER BY entity_name, pattern_type
        LIMIT %s
    """, (limit,)).fetchall()


def structure_observation_matrix(conn, observation_type=None, search=None, limit=50, offset=0):
    clauses = []
    params = []
    if observation_type:
        clauses.append("observation_type = %s")
        params.append(observation_type)
    if search:
        clauses.append("(company_name ILIKE %s OR company_regnr ILIKE %s)")
        q = f"%{search}%"
        params.extend([q, q])

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    total = conn.execute(f"SELECT count(*) AS n FROM v_structure_observations {where_sql}", params).fetchone()["n"]
    offset = clamp_offset(offset, total, limit)  # F-6: ?page=1e20 -> last page

    params.extend([limit, offset])
    rows = conn.execute(f"""
    SELECT company_id, company_name, company_regnr, observation_type, observation_code,
           observation_label, observed_pct, related_entity_id, related_entity_name
    FROM v_structure_observations
    {where_sql}
    ORDER BY company_name, observation_type
    LIMIT %s OFFSET %s
""", params).fetchall()
    return {"total": total, "rows": rows, "limit": limit, "offset": offset}


def entity_structure_observations(conn, company_id):
    return conn.execute("""
    SELECT observation_type, observation_code, observation_label, observed_pct,
           related_entity_id, related_entity_name
    FROM v_structure_observations
    WHERE company_id = %s
    ORDER BY observation_type
""", (company_id,)).fetchall()
