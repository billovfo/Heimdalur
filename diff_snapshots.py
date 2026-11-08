#!/usr/bin/env python3
"""Automated Snapshot Diffing Batch Script (diff_snapshots.py).

Tracks, diffs and records historical change events across Heimdal snapshots.
Run standalone with HEIMDAL_DB (or --db) naming the target database; nothing triggers it.
"""
import os
import sys
import logging
import argparse
import psycopg
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("diff_snapshots")

DB_URI = os.environ.get("HEIMDAL_DB")  # required, no default (A-15)


def record_change_event(conn, canonical_entity_id, change_category, field_name, old_value, new_value, summary, related_entity_id=None, related_entity_name=None):
    """Record a single structured change event in entity_change_event."""
    row = conn.execute("""
        INSERT INTO entity_change_event (
            canonical_entity_id, change_category, field_name, old_value, new_value, 
            related_entity_id, related_entity_name, change_summary
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s
        ) RETURNING event_id;
    """, (
        canonical_entity_id,
        change_category,
        field_name,
        str(old_value) if old_value is not None else None,
        str(new_value) if new_value is not None else None,
        related_entity_id,
        related_entity_name,
        summary
    )).fetchone()
    
    conn.commit()
    return row["event_id"] if row else None


def detect_relation_changes(conn):
    """Detect and record changes between current relation graph and snapshot history."""
    changes_recorded = 0
    logger.info("Checking for new unrecorded state changes...")
    
    # Check for control actors not yet registered as change events
    new_ubos = conn.execute("""
        SELECT cr.relation_id, cr.registered_id, cr.actor_id, ce_comp.display_name AS comp_name, ce_act.display_name AS act_name
        FROM v_control_registration cr
        JOIN v_entity ce_comp ON ce_comp.canonical_entity_id = cr.registered_id
        JOIN v_entity ce_act ON ce_act.canonical_entity_id = cr.actor_id
        LEFT JOIN entity_change_event ece 
            ON ece.canonical_entity_id = cr.registered_id 
            AND ece.change_category = 'CONTROL_ACTOR_CHANGED'
            AND ece.related_entity_id = cr.actor_id
        WHERE ece.event_id IS NULL
        LIMIT 50;
    """).fetchall()
    
    for u in new_ubos:
        record_change_event(
            conn,
            canonical_entity_id=u["registered_id"],
            change_category="CONTROL_ACTOR_CHANGED",
            field_name="control_actor",
            old_value=None,
            new_value=u["act_name"],
            summary=f"Ny kontrolaktør registreret: {u['act_name']}",
            related_entity_id=u["actor_id"],
            related_entity_name=u["act_name"]
        )
        changes_recorded += 1
        
    logger.info("Diff completed. %d new change event(s) recorded.", changes_recorded)
    return {"changes_recorded": changes_recorded}


def main():
    parser = argparse.ArgumentParser(description="Heimdal Automated Snapshot Diffing Engine")
    parser.add_argument("--db", default=DB_URI, help="PostgreSQL connection URI")
    args = parser.parse_args()
    if not args.db:
        parser.error("set HEIMDAL_DB or pass --db (no default: never live heimdal by accident)")

    with psycopg.connect(args.db, row_factory=dict_row) as conn:
        res = detect_relation_changes(conn)
        print(f"Diff complete: {res['changes_recorded']} changes recorded.")


if __name__ == "__main__":
    main()
