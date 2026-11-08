-- HISTORICAL (audit B-17, 2026-11-06): the canonical DB's schema is the ledger in
-- sql/migrations/ (python3 -m heimdal.canonical_migrations). Change it there, not here.
-- ============================================================================
-- Heimdal PEP (Politically Exposed Persons) & Public Office Schema
-- ============================================================================
-- Tables and views only (audit 2026-11 B-6/C-5, owner D14). No seed: PEP rows
-- come only from a sourced, dated import that sets canonical_entity_id, the
-- identity link. A person is never matched by name: 93 persons share a display
-- name with another person. Rows without the link flag nobody, so with no
-- import the feature is empty. Idempotent; one transaction.
BEGIN;

CREATE TABLE IF NOT EXISTS pep_record (
    pep_id SERIAL PRIMARY KEY,
    person_name TEXT NOT NULL, -- as written by the source; never shown, never matched
    office_title TEXT NOT NULL,
    pep_category TEXT NOT NULL, -- 'POLITICAL', 'EXECUTIVE', 'MUNICIPAL', 'STATE_ENTERPRISE'
    period_start TEXT,
    period_end TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The identity link. Nullable for tables created before it; set only by the import.
ALTER TABLE pep_record ADD COLUMN IF NOT EXISTS canonical_entity_id BIGINT
    REFERENCES canonical_entity (canonical_entity_id);
DROP INDEX IF EXISTS idx_pep_name;
CREATE INDEX IF NOT EXISTS idx_pep_entity ON pep_record (canonical_entity_id);

-- PEP records joined to their linked canonical person, by id only.
CREATE OR REPLACE VIEW v_pep_actors AS
SELECT
    ce.canonical_entity_id AS canonical_person_id,
    ce.display_name AS person_name,
    pr.pep_id,
    pr.office_title,
    pr.pep_category,
    pr.period_start,
    pr.period_end
FROM pep_record pr
JOIN canonical_entity ce ON ce.canonical_entity_id = pr.canonical_entity_id AND ce.entity_kind = 'PERSON';

GRANT SELECT ON pep_record TO heimdal_reader;
GRANT SELECT ON v_pep_actors TO heimdal_reader;

COMMIT;
