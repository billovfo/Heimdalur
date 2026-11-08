"""PERSON-kind gates for the review web tier (P1.1 consolidation).

Single canonical implementation for routes/service-side canonical-kind checks.
human_links.py keeps its own inline check ON PURPOSE: the application tier must
never import review code (architecture guard). Do not "consolidate" across
that boundary.
"""
from __future__ import annotations


def kind_of_canonical(canonical_conn, canonical_entity_id: int):
    row = canonical_conn.execute(
        "SELECT entity_kind FROM canonical_entity "
        "WHERE canonical_entity_id = %s", (canonical_entity_id,)).fetchone()
    if row is None:
        return None
    return row["entity_kind"] if isinstance(row, dict) else row[0]


def assert_person_kind(canonical_conn, canonical_entity_id: int) -> None:
    """Raise ValueError unless the canonical is an existing PERSON."""
    if kind_of_canonical(canonical_conn, canonical_entity_id) != "PERSON":
        raise ValueError(
            "canonical %s is not an existing PERSON" % (canonical_entity_id,))
