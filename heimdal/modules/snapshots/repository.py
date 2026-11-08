"""SQL queries for snapshots — change feed from v_entity_change_feed."""


def select_change_feed(conn, where_sql, params):
    """Execute the change feed query with an optional WHERE clause."""
    rows = conn.execute(f"""
        SELECT event_id, canonical_entity_id, entity_name, entity_kind, registration_number,
               event_timestamp, change_category, field_name, old_value, new_value,
               related_entity_id, related_entity_name, change_summary
        FROM v_entity_change_feed
        {where_sql}
        ORDER BY event_timestamp DESC, event_id DESC
        LIMIT %s OFFSET %s
    """, params).fetchall()
    return [dict(r) for r in rows]
