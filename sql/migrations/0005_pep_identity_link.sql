-- 0005 (W4b, 3af7f3f): PEP records flag a person by identity link only, never by
-- name (audit B-6/C-5, owner D14). Idempotent.
ALTER TABLE public.pep_record ADD COLUMN IF NOT EXISTS canonical_entity_id BIGINT
    REFERENCES public.canonical_entity (canonical_entity_id);
DROP INDEX IF EXISTS public.idx_pep_name;
CREATE INDEX IF NOT EXISTS idx_pep_entity ON public.pep_record (canonical_entity_id);

CREATE OR REPLACE VIEW public.v_pep_actors AS
SELECT
    ce.canonical_entity_id AS canonical_person_id,
    ce.display_name AS person_name,
    pr.pep_id,
    pr.office_title,
    pr.pep_category,
    pr.period_start,
    pr.period_end
FROM public.pep_record pr
JOIN public.canonical_entity ce ON ce.canonical_entity_id = pr.canonical_entity_id AND ce.entity_kind = 'PERSON';

-- The only data step in the ledger: a row without the identity link is the old
-- name-matched seed (5 rows in the 2026-09-04 dump; the dev DB has 0). A sourced
-- import always sets the link.
DELETE FROM public.pep_record WHERE canonical_entity_id IS NULL;
