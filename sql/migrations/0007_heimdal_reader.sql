-- 0007 (W5b): heimdal_reader is the portal's read-only login (audit B-9).
-- - LOGIN without a password here: the owner sets one (scram) and adds a pgpass line.
-- - SELECT only on what the portal reads: registry_ownership, registry_analytics,
--   the public read views and 8 public tables. No write privilege anywhere; the
--   portal's few writes (watchlist toggle) go through shared.db.tx() and
--   HEIMDAL_PG_DSN. No enumeration_key, raw bodies, *_legacy or fetch records.
-- - default_transaction_read_only=on for this role in this DB (second line of defence).
-- Idempotent; every grant is explicit, so a restore with or without ACLs ends the same.
-- Owner, once per cluster (no password is ever written to the repo; auth stays scram):
--   psql -h 127.0.0.1 -U postgres -c '\password heimdal_reader'
--   then add to tools/.dev-pgpass (mode 0600):
--     localhost:5432:*:heimdal_reader:<that password>
--     127.0.0.1:5432:*:heimdal_reader:<that password>
-- New public relations the portal reads need a GRANT in a new ledger file; tables
-- added to registry_ownership / registry_analytics are readable by default privilege.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'heimdal_reader') THEN
    CREATE ROLE heimdal_reader LOGIN;
  END IF;
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO heimdal_reader', current_database());
  EXECUTE format('ALTER ROLE heimdal_reader IN DATABASE %I SET default_transaction_read_only = on',
                 current_database());
END
$$;

-- Start from nothing: the 2026-09 ACLs gave it write on user_watchlist and read on raw tables.
REVOKE ALL ON ALL TABLES IN SCHEMA public, registry_ownership, registry_analytics FROM heimdal_reader;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public, registry_ownership, registry_analytics FROM heimdal_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM heimdal_reader;

GRANT USAGE ON SCHEMA public, registry_ownership, registry_analytics TO heimdal_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA registry_ownership, registry_analytics TO heimdal_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA registry_ownership, registry_analytics
    GRANT SELECT ON TABLES TO heimdal_reader;
GRANT SELECT ON
    public.canonical_entity, public.company_inventory, public.entity_status,
    public.collection_run, public.geo_postcode, public.company_location,
    public.entity_change_event, public.user_watchlist,
    public.v_entity, public.v_direct_ownership, public.v_control_registration,
    public.v_role_relation, public.v_source_anomaly, public.v_provenance,
    public.v_ownership_cycle_observations, public.v_same_surname_share_observations,
    public.v_structure_observations, public.v_registration_patterns,
    public.v_entity_change_feed, public.v_pep_actors, public.v_user_watchlist
TO heimdal_reader;
