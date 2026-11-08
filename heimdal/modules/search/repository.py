"""SQL queries for entity search."""


def search_entities_digit(conn, q, regnr_like, like):
    """Search when query is a digit (match by ID, regnr, or name)."""
    return conn.execute(
        "SELECT canonical_entity_id, display_name, entity_kind, registration_number, inventory_status "
        "FROM v_entity WHERE canonical_entity_id = %s "
        "OR (registration_number IS NOT NULL AND registration_number ILIKE %s) OR display_name ILIKE %s "
        "ORDER BY display_name LIMIT 15",
        (int(q), regnr_like or like, like)
    ).fetchall()


def search_entities_text(conn, regnr_like, like, prefix_like):
    """Search when query is text (match by regnr or name with prefix ordering)."""
    return conn.execute(
        "SELECT canonical_entity_id, display_name, entity_kind, registration_number, inventory_status "
        "FROM v_entity WHERE (registration_number IS NOT NULL AND registration_number ILIKE %s) OR display_name ILIKE %s "
        "ORDER BY CASE WHEN display_name ILIKE %s THEN 1 ELSE 2 END, display_name LIMIT 15",
        (regnr_like or like, like, prefix_like)
    ).fetchall()
