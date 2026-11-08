"""SQL queries for watchlist — SELECT/INSERT/DELETE from user_watchlist."""


def select_watchlist(conn):
    return conn.execute("SELECT * FROM v_user_watchlist ORDER BY added_at DESC").fetchall()


def entity_kind(conn, canonical_entity_id):
    """canonical_entity's kind, or None when there is no such entity (F-14)."""
    row = conn.execute("SELECT entity_kind FROM canonical_entity WHERE canonical_entity_id = %s",
                       (canonical_entity_id,)).fetchone()
    return row and row["entity_kind"]


def toggle_watchlist(conn, canonical_entity_id, entity_kind, notes=None):
    """-> "added" | "removed". Each write is one atomic statement; `conn` is a
    write transaction (shared.db.tx), which commits."""
    if conn.execute("DELETE FROM user_watchlist WHERE canonical_entity_id = %s RETURNING 1",
                    (canonical_entity_id,)).fetchone():
        return "removed"
    conn.execute("INSERT INTO user_watchlist (canonical_entity_id, entity_kind, notes)"
                 " VALUES (%s, %s, %s) ON CONFLICT (canonical_entity_id) DO NOTHING",
                 (canonical_entity_id, entity_kind, notes))
    return "added"


def select_watchlist_change_feed(conn, limit):
    return conn.execute("""
        SELECT e.*
        FROM v_entity_change_feed e  -- has entity_name/entity_kind for the links
        JOIN user_watchlist w ON w.canonical_entity_id = e.canonical_entity_id
        ORDER BY e.event_timestamp DESC, e.event_id DESC
        LIMIT %s
    """, (limit,)).fetchall()
