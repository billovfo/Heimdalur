"""Apply heimdal/pdfhist/projection_migration.sql in the exact CREATE order.

Isolation contract (task "ISOLERET DATABASE-LAG", sections 2/4/40): this
migration creates ONLY the registry_projection schema inside the PDF-history
database. It never touches evidence tables (they are only referenced by FKs
from projection to evidence) and never touches the heimdal QA/production
database.
"""
from __future__ import annotations

from pathlib import Path

import psycopg

from .migrate import split_statements

MIGRATION_PATH = Path(__file__).with_name("projection_migration.sql")


def apply_projection_migration(conn: psycopg.Connection) -> list[str]:
    """Execute the projection migration statements in order."""
    statements = split_statements(MIGRATION_PATH.read_text(encoding="utf-8"))
    executed = []
    for statement in statements:
        conn.execute(statement)
        executed.append(" ".join(statement.split())[:60])
    return executed