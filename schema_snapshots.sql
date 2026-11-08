-- HISTORICAL (audit B-17, 2026-11-06): the canonical DB's schema is the ledger in
-- sql/migrations/ (python3 -m heimdal.canonical_migrations). Change it there, not here.
-- ============================================================================
-- Heimdal Multi-Snapshot Diffing & Historical Change Tracking (Feature 5)
-- ============================================================================

CREATE TABLE IF NOT EXISTS entity_change_event (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_entity_id BIGINT NOT NULL REFERENCES canonical_entity(canonical_entity_id),
    event_timestamp TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    change_category TEXT NOT NULL CHECK (
        change_category IN (
            'OWNERSHIP_ADDED',
            'OWNERSHIP_REMOVED',
            'OWNERSHIP_PERCENTAGE_CHANGED',
            'ROLE_APPOINTED',
            'ROLE_RESIGNED',
            'CONTROL_ACTOR_CHANGED',
            'STATUS_CHANGED'
        )
    ),
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    related_entity_id BIGINT REFERENCES canonical_entity(canonical_entity_id),
    related_entity_name TEXT,
    change_summary TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entity_change_event_entity ON entity_change_event(canonical_entity_id, event_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_entity_change_event_category ON entity_change_event(change_category, event_timestamp DESC);

-- B-7: this file creates tables only. Events come from real snapshot diffs
-- (diff_snapshots.py), never from a seed. Natural key: one entity, one change
-- (field, old -> new, related entity) at one moment; NULLs compare equal, so a
-- replayed event cannot be stored twice.
CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_change_event_natural ON entity_change_event
    (canonical_entity_id, change_category, field_name, old_value, new_value, related_entity_id, event_timestamp)
    NULLS NOT DISTINCT;

-- View: Unified change feed with human-readable titles and entity badges
CREATE OR REPLACE VIEW v_entity_change_feed AS
SELECT 
    e.event_id,
    e.canonical_entity_id,
    ce.display_name AS entity_name,
    ce.entity_kind,
    ce.registration_number,
    e.event_timestamp,
    e.change_category,
    e.field_name,
    e.old_value,
    e.new_value,
    e.related_entity_id,
    e.related_entity_name,
    e.change_summary
FROM entity_change_event e
JOIN v_entity ce ON ce.canonical_entity_id = e.canonical_entity_id;

GRANT SELECT ON entity_change_event TO heimdal_reader;
GRANT SELECT ON v_entity_change_feed TO heimdal_reader;
