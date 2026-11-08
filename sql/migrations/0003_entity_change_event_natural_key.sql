-- 0003 (W3c, 1fc7b5e): a replayed change event cannot be stored twice (audit B-7).
-- schema_snapshots.sql gained this index, but it was never applied to the dev
-- DB; it has 0 duplicates on the key. Idempotent.
CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_change_event_natural ON public.entity_change_event
    (canonical_entity_id, change_category, field_name, old_value, new_value, related_entity_id, event_timestamp)
    NULLS NOT DISTINCT;
