-- HISTORICAL (audit B-17, 2026-11-06): the canonical DB's schema is the ledger in
-- sql/migrations/ (python3 -m heimdal.canonical_migrations). Change it there, not here.
-- Phase 3 targeted closure (M-01) — nullable mixed-source pair metadata.
-- Idempotent. Safe: column-level only; grain (ppo_grain), co_participation_class,
-- canonical ids, role_context and known_overlap semantics are NOT altered.
-- NULL source_family     = pair fact supported by more than one source family
-- NULL authority_class   = pair fact supported by more than one authority class
-- (metadata incompleteness-by-design; reconstructible from same-build
--  observations/periods via the pair grain; never an invented 'MIXED' class).
-- Direct shared-source rows keep precise metadata (ppo_direct_metadata_present).

ALTER TABLE registry_analytics.person_person_overlap
    ALTER COLUMN source_family DROP NOT NULL;
ALTER TABLE registry_analytics.person_person_overlap
    ALTER COLUMN authority_class DROP NOT NULL;

ALTER TABLE registry_analytics.person_person_overlap DROP CONSTRAINT IF EXISTS ppo_direct_metadata_present;
ALTER TABLE registry_analytics.person_person_overlap ADD CONSTRAINT ppo_direct_metadata_present CHECK (
    co_participation_class NOT IN ('SAME_SNAPSHOT','SAME_OBSERVATION','KNOWN_TEMPORAL_OVERLAP')
    OR (source_family IS NOT NULL AND authority_class IS NOT NULL));

COMMENT ON COLUMN registry_analytics.person_person_overlap.source_family IS
    'Single contributing source family; NULL when the pair fact is supported by more than one family (heterogeneous fallback evidence; closure M-01)';
COMMENT ON COLUMN registry_analytics.person_person_overlap.authority_class IS
    'Single contributing authority class; NULL when the pair fact is supported by more than one authority class (heterogeneous fallback evidence; closure M-01)';