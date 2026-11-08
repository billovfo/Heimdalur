-- 0004 (W3d, 452dcf0): no orphan watchlist rows, and the view shows the
-- entity's own kind (audit F-14). Idempotent; the FK fails loudly on orphans.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_watchlist_entity'
                 AND conrelid = 'public.user_watchlist'::regclass) THEN
    ALTER TABLE public.user_watchlist ADD CONSTRAINT fk_watchlist_entity
      FOREIGN KEY (canonical_entity_id) REFERENCES public.canonical_entity (canonical_entity_id);
  END IF;
END
$$;

CREATE OR REPLACE VIEW public.v_user_watchlist AS
SELECT
    w.id AS watchlist_id,
    w.canonical_entity_id,
    ce.entity_kind,
    w.notes,
    w.created_at AS added_at,
    ce.display_name,
    ci.registration_number,
    ci.inventory_status
FROM public.user_watchlist w
JOIN public.canonical_entity ce ON ce.canonical_entity_id = w.canonical_entity_id
LEFT JOIN public.company_inventory ci ON ci.source_system = ce.source_system AND ci.source_entity_id = ce.source_entity_id;
