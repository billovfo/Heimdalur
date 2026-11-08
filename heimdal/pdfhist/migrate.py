"""Apply heimdal/pdfhist/migration.sql in the exact documented CREATE order.

v3.1.2 patch 1 makes the order binding: pdf_document_content is created BEFORE
pdf_document_observation, so the observation FK has an existing target in a
from-scratch run. No deferred FK, no ALTER workaround.
"""
from __future__ import annotations

import re
from pathlib import Path

import psycopg

from .db import require_test_db_name

MIGRATION_PATH = Path(__file__).with_name("migration.sql")


def split_statements(sql: str) -> list[str]:
    """Split on semicolons that terminate statements.

    The migration DDL deliberately contains no semicolons inside string or
    comment text, so a string-aware scan is sufficient and keeps the migration
    free of stored procedures (forbidden by the plan).
    """
    statements, current = [], []
    in_string = in_comment = False
    previous = ""
    for char in sql:
        if in_comment:
            if char == "\n":
                in_comment = False
            current.append(char)
            previous = char
            continue
        if not in_string and previous == "-" and char == "-":
            in_comment = True
            current.append(char)
            previous = char
            continue
        current.append(char)
        if not in_string and not in_comment and char == "'":
            in_string = True
        elif in_string and char == "'":
            in_string = False
        elif char == ";" and not in_string and not in_comment:
            statements.append("".join(current).strip())
            current = []
        previous = char
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s and not all(
        line.strip().startswith("--") or not line.strip()
        for line in s.splitlines())]


def apply_migration(conn: psycopg.Connection) -> list[str]:
    """Execute the migration statements in order; return executed statements."""
    statements = split_statements(MIGRATION_PATH.read_text(encoding="utf-8"))
    executed = []
    for statement in statements:
        conn.execute(statement)
        executed.append(re.sub(r"\s+", " ", statement)[:60])
    return executed


def bootstrap_test_database(
    admin_dsn_str: str,
    database: str,
    *,
    force_drop: bool = False,
) -> str:
    """Create a fresh dedicated test database (drop any previous incarnation).

    Touches ONLY the throwaway test database - never the heimdal/QA database:
    a name that fails db.require_test_db_name is refused before connecting,
    unless the caller passes force_drop=True (pdfhist_pilot_setup --force-drop).
    """
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,40}", database):
        raise ValueError(f"unsafe test database name: {database!r}")
    if not force_drop:
        require_test_db_name(database)
    with psycopg.connect(admin_dsn_str, autocommit=True) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone()
        if exists:
            admin.execute(f"DROP DATABASE {database}")
        admin.execute(f"CREATE DATABASE {database}")
    root = admin_dsn_str.rsplit("/", 1)[0]
    return f"{root}/{database}"