"""Watchlist: entity monitoring and change feed."""
from heimdal.modules.shared.db import tx
from heimdal.modules.watchlist import repository


def get_watchlist(conn):
    """Retrieve all entities in the user's watchlist."""
    return repository.select_watchlist(conn)


def toggle_watchlist(conn, canonical_entity_id, notes=None):
    """Add or remove an entity: "added" | "removed" | None when it does not exist.
    The lookup runs on `conn`, the request's read-only connection; the write
    gets its own transaction on HEIMDAL_PG_DSN (B-9). A bad id writes nothing."""
    kind = repository.entity_kind(conn, canonical_entity_id)
    if kind is None:
        return None
    with tx() as w:
        return repository.toggle_watchlist(w, canonical_entity_id, kind, notes)


def watchlist_change_feed(conn, limit=20):
    """Retrieve recent change events exclusively for monitored entities."""
    return repository.select_watchlist_change_feed(conn, limit)
