-- HISTORICAL (audit B-17, 2026-11-06): the canonical DB's schema is the ledger in
-- sql/migrations/ (python3 -m heimdal.canonical_migrations). Change it there, not here.
-- ============================================================================
-- Heimdal User Watchlist & Portfolio Monitoring Schema
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_watchlist (
    id SERIAL PRIMARY KEY,
    canonical_entity_id BIGINT NOT NULL,
    entity_kind TEXT NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_watchlist_entity UNIQUE (canonical_entity_id)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_entity ON user_watchlist(canonical_entity_id);

-- Audit F-14: no orphan rows. Idempotent (also for tables created before the FK);
-- it fails loudly if orphans exist, instead of deleting them.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_watchlist_entity'
                 AND conrelid = 'user_watchlist'::regclass) THEN
    ALTER TABLE user_watchlist ADD CONSTRAINT fk_watchlist_entity
      FOREIGN KEY (canonical_entity_id) REFERENCES canonical_entity (canonical_entity_id);
  END IF;
END
$$;

CREATE OR REPLACE VIEW v_user_watchlist AS
SELECT 
    w.id AS watchlist_id,
    w.canonical_entity_id,
    ce.entity_kind,  -- F-14: the entity's kind, not the one stored at toggle time
    w.notes,
    w.created_at AS added_at,
    ce.display_name,
    ci.registration_number,
    ci.inventory_status
FROM user_watchlist w
JOIN canonical_entity ce ON ce.canonical_entity_id = w.canonical_entity_id
LEFT JOIN company_inventory ci ON ci.source_system = ce.source_system AND ci.source_entity_id = ce.source_entity_id;

GRANT SELECT, INSERT, UPDATE, DELETE ON user_watchlist TO postgres;
GRANT SELECT, INSERT, UPDATE, DELETE ON user_watchlist TO heimdal_reader;
GRANT USAGE, SELECT ON SEQUENCE user_watchlist_id_seq TO heimdal_reader;
GRANT SELECT ON v_user_watchlist TO heimdal_reader;
