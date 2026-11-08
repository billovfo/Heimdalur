-- HISTORICAL (audit B-17, 2026-11-06): the canonical DB's schema is the ledger in
-- sql/migrations/ (python3 -m heimdal.canonical_migrations). Change it there, not here.
-- HEIMDAL — registry_ownership FK constraint migration (Phase 1 closure correction).
--
-- Context: Phase 1 created the target tables with
--   CREATE TABLE registry_ownership.<t> (LIKE public.<t>_legacy INCLUDING ALL)
-- PostgreSQL's LIKE ... INCLUDING ALL copies PK/UNIQUE/CHECK constraints,
-- defaults, identity and indexes, but does NOT copy FOREIGN KEY constraints
-- (documented PostgreSQL limitation). The Phase 1 cutover therefore carried the
-- correct DATA (context rows resolve inside registry_ownership.relation; all
-- source_attempt_ids resolve in public.request_attempt — verified 0 violations
-- before this migration) but was missing the four declared FK CONSTRAINTS.
--
-- This migration adds them with full validation. Idempotent: identical outcome
-- on every run. Data is NOT moved or modified — constraints only.
--
-- Note: the legacy tables keep their original FKs to canonical_relation_legacy,
-- so the rollback path keeps its own internal referential integrity.

DO $$
BEGIN
    -- relation.source_attempt_id -> shared public acquisition (Phase 1 B4)
    ALTER TABLE registry_ownership.relation
        DROP CONSTRAINT IF EXISTS relation_source_attempt_id_fkey;
    ALTER TABLE registry_ownership.relation
        ADD CONSTRAINT relation_source_attempt_id_fkey
        FOREIGN KEY (source_attempt_id) REFERENCES public.request_attempt (attempt_id);

    -- context FKs rewired to the target relation table (Phase 1 B8 semantics)
    ALTER TABLE registry_ownership.company_relation_context
        DROP CONSTRAINT IF EXISTS company_relation_context_source_relation_id_fkey;
    ALTER TABLE registry_ownership.company_relation_context
        ADD CONSTRAINT company_relation_context_source_relation_id_fkey
        FOREIGN KEY (source_relation_id) REFERENCES registry_ownership.relation (relation_id);

    ALTER TABLE registry_ownership.person_relation_context
        DROP CONSTRAINT IF EXISTS person_relation_context_person_a_relation_id_fkey;
    ALTER TABLE registry_ownership.person_relation_context
        ADD CONSTRAINT person_relation_context_person_a_relation_id_fkey
        FOREIGN KEY (person_a_relation_id) REFERENCES registry_ownership.relation (relation_id);

    ALTER TABLE registry_ownership.person_relation_context
        DROP CONSTRAINT IF EXISTS person_relation_context_person_b_relation_id_fkey;
    ALTER TABLE registry_ownership.person_relation_context
        ADD CONSTRAINT person_relation_context_person_b_relation_id_fkey
        FOREIGN KEY (person_b_relation_id) REFERENCES registry_ownership.relation (relation_id);
END $$;

-- Validation is enforced (default WITH VALIDATION): the migration fails loudly
-- if any row would violate the declared semantics.