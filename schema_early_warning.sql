-- HISTORICAL (audit B-17, 2026-11-06): the canonical DB's schema is the ledger in
-- sql/migrations/ (python3 -m heimdal.canonical_migrations). Change it there, not here.
-- ============================================================================
-- Heimdal registration-pattern observations (schema_early_warning.sql)
--
-- The early-warning risk view is removed: every caller now reads the neutral
-- v_registration_patterns surface.
-- ============================================================================

DROP VIEW IF EXISTS v_early_warning_signals;

-- Neutral registration-pattern observations. Only directly observable
-- quantities; cycles are already ownership observations and are not
-- duplicated here.
CREATE OR REPLACE VIEW v_registration_patterns AS
SELECT ce.canonical_entity_id AS entity_id,
       ce.display_name AS entity_name,
       ce.entity_kind,
       'MANY_REGISTERED_ROLES' AS pattern_type,
       'Registreret med roller i ' || count(DISTINCT vrr.company_id)::text || ' selskaber' AS pattern_label,
       count(DISTINCT vrr.company_id)::numeric AS observed_value
FROM canonical_entity ce
JOIN v_role_relation vrr ON vrr.person_id = ce.canonical_entity_id
WHERE ce.entity_kind = 'PERSON'
GROUP BY ce.canonical_entity_id, ce.display_name, ce.entity_kind
HAVING count(DISTINCT vrr.company_id) >= 5
UNION ALL
SELECT ce.canonical_entity_id,
       ce.display_name,
       ce.entity_kind,
       'DIRECT_SHARE_SUM_OVER_100',
       'Registrerede direkte ejerandele summerer til '
         || round(sum(vdo.parsed_percentage)::numeric, 1)::text || '%',
       round(sum(vdo.parsed_percentage)::numeric, 1)
FROM canonical_entity ce
JOIN v_direct_ownership vdo ON vdo.owned_id = ce.canonical_entity_id
WHERE ce.entity_kind = 'COMPANY'
GROUP BY ce.canonical_entity_id, ce.display_name, ce.entity_kind
HAVING sum(vdo.parsed_percentage) > 100.0;

GRANT SELECT ON v_registration_patterns TO heimdal_reader;
