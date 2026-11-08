-- HISTORICAL (audit B-17, 2026-11-06): the canonical DB's schema is the ledger in
-- sql/migrations/ (python3 -m heimdal.canonical_migrations). Change it there, not here.
-- Heimdal read layer (Fase 0 of the portal/API tier, owner-authorized
-- 2026-08-02). Views only — no tables, nothing to migrate, nothing to keep in
-- sync by hand. Every view is built so the structural separations the
-- product requires are impossible to violate by construction, not just by
-- convention:
--
--   * v_direct_ownership carries no REGISTERED_CONTROL_ACTOR rows and no
--     percentage-shaped column exists on v_control_registration at all
--     (§14a.1: a control actor is never an ownership fact).
--   * Every view keys on canonical_entity_id, never on raw_*_source_entity_id
--     — a query written against these views cannot accidentally leak a
--     fetch-subject-vs-edge-subject direction bug (§16.2's historical
--     865-false-positive lesson) because the raw transport columns aren't
--     exposed here at all.
--   * display_name is always the PARSED name (see canonical_entity's own
--     comment in schema.sql) — a private person's home address never
--     surfaces through these views.
--
-- Apply after schema.sql and after `assign-canonical-ids` has run at least
-- once (every view joins through canonical_entity; an entity without a
-- canonical ID yet simply doesn't appear).
--
-- Read-only at the connection level: see the role/grant statements at the
-- bottom. No view here is ever written to by the collector or by an API
-- layer — apply plain SELECT-only intent throughout.

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

CREATE OR REPLACE VIEW v_entity AS
SELECT ce.canonical_entity_id, ce.source_system, ce.source_entity_id,
       ce.entity_kind, ce.entity_kind_confidence, ce.raw_type_token, ce.display_name,
       ci.registration_number, ci.inventory_status, ci.typing_status,
       ci.raw_display_name AS raw_inventory_label
FROM canonical_entity ce
LEFT JOIN company_inventory ci
  ON ci.source_system = ce.source_system AND ci.source_entity_id = ce.source_entity_id;

-- DIRECT_OWNERSHIP only. No raw_from/raw_to columns, no REGISTERED_CONTROL_ACTOR
-- rows can appear here — the WHERE clause is the only thing standing between
-- "ownership" and "control registration", and it can't be bypassed by a typo
-- in a join elsewhere the way a shared raw column could be.
CREATE OR REPLACE VIEW v_direct_ownership AS
SELECT cr.relation_id,
       ceo.canonical_entity_id AS owner_id, ceo.display_name AS owner_name, ceo.entity_kind AS owner_kind,
       cew.canonical_entity_id AS owned_id, cew.display_name AS owned_name, cew.entity_kind AS owned_kind,
       cr.parsed_percentage, cr.raw_percentage_label, cr.percentage_parse_status,
       cr.semantic_direction_confidence, cr.source_attempt_id
FROM canonical_relation cr
JOIN canonical_entity ceo ON ceo.source_entity_id = cr.canonical_subject_entity_id
JOIN canonical_entity cew ON cew.source_entity_id = cr.canonical_object_entity_id
WHERE cr.canonical_relation_type = 'DIRECT_OWNERSHIP';

-- REGISTERED_CONTROL_ACTOR only. Deliberately NO percentage column exists —
-- §14a.1: "ownership_percentage=NOT_APPLICABLE" is enforced by the column's
-- absence, not by a convention a later query could ignore.
CREATE OR REPLACE VIEW v_control_registration AS
SELECT cr.relation_id,
       cea.canonical_entity_id AS actor_id, cea.display_name AS actor_name, cea.entity_kind AS actor_kind,
       cee.canonical_entity_id AS registered_id, cee.display_name AS registered_name,
       cr.raw_source_relation_label, cr.semantic_direction_confidence, cr.source_attempt_id
FROM canonical_relation cr
JOIN canonical_entity cea ON cea.source_entity_id = cr.canonical_subject_entity_id
JOIN canonical_entity cee ON cee.source_entity_id = cr.canonical_object_entity_id
WHERE cr.canonical_relation_type = 'REGISTERED_CONTROL_ACTOR';

-- Nevnd/Stjórn/Annað role relations. Direction is UNCONFIRMED for these
-- cbTypeIds (§4), so the person side and company side are derived from
-- entity_kind='PERSON' (itself INFERRED, see canonical_entity), never from
-- raw From/To. The person/company side is guaranteed to differ by the WHERE
-- clause; the 8 company<->company O.V.E. anomalies fall out of this view
-- entirely and land in v_source_anomaly instead.
CREATE OR REPLACE VIEW v_role_relation AS
SELECT cr.relation_id,
       CASE WHEN cef.entity_kind = 'PERSON' THEN cef.canonical_entity_id ELSE cet.canonical_entity_id END AS person_id,
       CASE WHEN cef.entity_kind = 'PERSON' THEN cef.display_name ELSE cet.display_name END AS person_name,
       CASE WHEN cef.entity_kind = 'PERSON' THEN cet.canonical_entity_id ELSE cef.canonical_entity_id END AS company_id,
       CASE WHEN cef.entity_kind = 'PERSON' THEN cet.display_name ELSE cef.display_name END AS company_name,
       cr.inferred_group, cr.inferred_group_confidence, cr.canonical_role_label,
       cr.raw_source_relation_label, cr.raw_source_relation_type_id,
       cr.semantic_direction_confidence, cr.semantic_status, cr.source_attempt_id
FROM canonical_relation cr
JOIN canonical_entity cef ON cef.source_entity_id = cr.raw_from_source_entity_id
JOIN canonical_entity cet ON cet.source_entity_id = cr.raw_to_source_entity_id
WHERE cr.canonical_relation_type = 'OTHER_SOURCE_RELATION'
  AND (cef.entity_kind = 'PERSON') <> (cet.entity_kind = 'PERSON');

-- The §14a.2 anomaly (O.V.E. with From <> To), surfaced with resolved names,
-- never hidden and never guessed at.
CREATE OR REPLACE VIEW v_source_anomaly AS
SELECT cr.relation_id,
       cef.canonical_entity_id AS from_id, cef.display_name AS from_name,
       cet.canonical_entity_id AS to_id, cet.display_name AS to_name,
       cr.raw_source_relation_type_id, cr.raw_source_relation_label,
       cr.semantic_status, cr.semantic_direction_confidence, cr.source_attempt_id
FROM canonical_relation cr
JOIN canonical_entity cef ON cef.source_entity_id = cr.raw_from_source_entity_id
JOIN canonical_entity cet ON cet.source_entity_id = cr.raw_to_source_entity_id
WHERE cr.semantic_status = 'UNRESOLVED_SOURCE_ANOMALY';

-- §10/§24 freshness, from request_attempt only. A canonical entity that was
-- never itself the SUBJECT of a get-table request (e.g. every person, and
-- the 192 foreign/non-inventoried companies) has no row here — the API layer
-- must render that as "never directly fetched", not as missing data.
CREATE OR REPLACE VIEW v_provenance AS
SELECT ce.canonical_entity_id, ce.source_entity_id,
       max(ra.requested_at) AS last_fetched_at,
       (array_agg(ra.response_sha256 ORDER BY ra.requested_at DESC))[1] AS snapshot_sha256,
       (array_agg(ra.http_status     ORDER BY ra.requested_at DESC))[1] AS last_http_status,
       (array_agg(ra.error_category  ORDER BY ra.requested_at DESC))[1] AS last_error_category
FROM canonical_entity ce
JOIN request_attempt ra ON ra.requested_source_entity_id = ce.source_entity_id
WHERE ra.endpoint = 'get-table'
GROUP BY 1, 2;

-- Read-only role for the future API layer (owner decision 2026-08-02: kun
-- localhost, ingen skriveadgang). Password is a placeholder — set with
-- ALTER ROLE heimdal_reader WITH PASSWORD '...' before use, or switch the API
-- process to peer/local trust like the collector already does.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'heimdal_reader') THEN
    CREATE ROLE heimdal_reader LOGIN;
  END IF;
END
$$;

-- current_database(), never a literal: applying this to a copy must not
-- change the ACL of live heimdal (audit B-10).
DO $$ BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO heimdal_reader', current_database());
END $$;
GRANT USAGE ON SCHEMA public TO heimdal_reader;
GRANT SELECT ON
  v_entity, v_direct_ownership, v_control_registration, v_role_relation,
  v_source_anomaly, v_provenance, canonical_entity, canonical_relation,
  entity_status, indirect_ownership, ownership_cycle_member,
  person_relation_context, company_relation_context, company_inventory,
  request_attempt, collection_run
TO heimdal_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO heimdal_reader;

COMMIT;
