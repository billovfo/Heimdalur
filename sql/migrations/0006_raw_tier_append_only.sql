-- 0006 (W5a, 28ed75f): a non-HTTP error keeps its text and no body (audit A-5),
-- and the raw tier is append-only for every role (audit A-14). Idempotent.
ALTER TABLE public.request_attempt ADD COLUMN IF NOT EXISTS error_text text;

CREATE OR REPLACE FUNCTION public.raw_tier_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% on %.% refused: the raw tier is append-only (audit A-14)',
    TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME;
END
$$;
CREATE OR REPLACE TRIGGER request_attempt_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON public.request_attempt
  FOR EACH STATEMENT EXECUTE FUNCTION public.raw_tier_append_only();
CREATE OR REPLACE TRIGGER response_content_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON public.response_content
  FOR EACH STATEMENT EXECUTE FUNCTION public.raw_tier_append_only();
