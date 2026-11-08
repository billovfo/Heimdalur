-- HISTORICAL (audit B-17, 2026-11-06): the canonical DB's schema is the ledger in
-- sql/migrations/ (python3 -m heimdal.canonical_migrations). Change it there, not here.
-- Heimdal structure-observation views (schema_compliance.sql)
--
-- The AML-named risk views are removed: every caller now reads the neutral
-- v_structure_observations surface. Absence-based and accusatory rules are
-- retired and must not return.

-- Post-cutover guard (audit 2026-11 B-3/A-6). This file is pre-cutover DDL.
-- After tools/phase1_cutover.py the ownership tables live in registry_ownership
-- and the public ones are *_legacy, so re-applying it would recreate them empty
-- and point the read views at them. The guard runs first, inside the file's
-- one transaction, so even a plain `psql -f` applies nothing.
BEGIN;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'registry_ownership')
     OR EXISTS (SELECT 1 FROM pg_tables
                WHERE schemaname = 'public' AND tablename LIKE '%\_legacy') THEN
    RAISE EXCEPTION 'refusing pre-cutover DDL on post-cutover database "%": schema registry_ownership or a public.*_legacy table exists (audit B-3)', current_database();
  END IF;
END
$$;

DROP VIEW IF EXISTS v_aml_compliance_overview;
DROP VIEW IF EXISTS v_aml_cycle_risk;
DROP VIEW IF EXISTS v_aml_family_clustering;

-- ----------------------------------------------------------------------------
-- Neutral structure observations.
-- These describe only patterns directly calculated from the local snapshot.
-- ----------------------------------------------------------------------------

-- 1. Ownership cycle observation
CREATE OR REPLACE VIEW v_ownership_cycle_observations AS
SELECT ce.canonical_entity_id AS company_id,
       ce.display_name AS company_name,
       ce.registration_number AS company_regnr,
       ocm.cycle_group_id
FROM ownership_cycle_member ocm
JOIN v_entity ce ON ce.source_entity_id = ocm.entity_id;

-- 2. Same-surname share observation
CREATE OR REPLACE VIEW v_same_surname_share_observations AS
WITH surname_shares AS (
    SELECT vdo.owned_id AS company_id,
           ce_comp.display_name AS company_name,
           ce_comp.registration_number AS company_regnr,
           split_part(vdo.owner_name, ' ', array_length(string_to_array(vdo.owner_name, ' '), 1)) AS surname,
           count(DISTINCT vdo.owner_id) AS person_count,
           sum(vdo.parsed_percentage) AS combined_percentage
    FROM v_direct_ownership vdo
    JOIN v_entity ce_comp ON ce_comp.canonical_entity_id = vdo.owned_id
    WHERE vdo.owner_kind = 'PERSON'
      AND vdo.parsed_percentage < 25.0
      AND vdo.parsed_percentage > 0
    GROUP BY vdo.owned_id, ce_comp.display_name, ce_comp.registration_number,
             split_part(vdo.owner_name, ' ', array_length(string_to_array(vdo.owner_name, ' '), 1))
    HAVING sum(vdo.parsed_percentage) >= 25.0
       AND count(DISTINCT vdo.owner_id) > 1
)
SELECT company_id, company_name, company_regnr,
       'SAME_SURNAME_SHARE' AS observation_type,
       'SAME_SURNAME_SHARE' AS observation_code,
       'Registrerede personer med efternavnet ' || surname
         || ' ejer samlet ' || round(combined_percentage, 1)::text || '% ('
         || person_count::text || ' personer)' AS observation_label,
       combined_percentage AS observed_pct,
       NULL::bigint AS related_entity_id,
       surname || ' (' || person_count::text || ' personer)' AS related_entity_name
FROM surname_shares;

-- 3. Unified neutral observation view
CREATE OR REPLACE VIEW v_structure_observations AS
SELECT company_id, company_name, company_regnr,
       'OWNERSHIP_CYCLE' AS observation_type,
       'OWNERSHIP_CYCLE' AS observation_code,
       'Lukket ejerrelation registreret (gruppe #' || cycle_group_id::text || ')' AS observation_label,
       NULL::numeric AS observed_pct,
       NULL::bigint AS related_entity_id,
       NULL::text AS related_entity_name
FROM v_ownership_cycle_observations
UNION ALL
SELECT company_id, company_name, company_regnr,
       observation_type, observation_code, observation_label, observed_pct,
       related_entity_id, related_entity_name
FROM v_same_surname_share_observations;

GRANT SELECT ON v_ownership_cycle_observations,
    v_same_surname_share_observations, v_structure_observations TO heimdal_reader;

COMMIT;
