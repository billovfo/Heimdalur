-- 0002 (W2, 9b6de6b): PDF-location fetch records, as schema.sql creates them,
-- without the FK to the foreign source.documents (audit B-4, owner D13).
-- Idempotent: the dev DB got this table from schema.sql on 2026-11-06.
-- heimdal_reader may not read it: 0007 grants only what the portal reads.
CREATE TABLE IF NOT EXISTS public.company_location_pdf_fetch (
    source_system text NOT NULL,
    source_entity_id bigint NOT NULL,
    source_document_id bigint NOT NULL,
    source_url text NOT NULL,
    response_sha256 char(64),
    fetched_at timestamptz NOT NULL DEFAULT now(),
    outcome text NOT NULL CHECK (outcome IN ('APPLIED', 'NO_LOCATION', 'FAILED')),
    http_status integer,
    failure_kind text CHECK (failure_kind IN ('HTTP_4XX', 'HTTP_5XX', 'NETWORK', 'INVALID_PDF')),
    attempt_count smallint NOT NULL CHECK (attempt_count BETWEEN 1 AND 2),
    PRIMARY KEY (source_system, source_entity_id),
    FOREIGN KEY (source_system, source_entity_id)
        REFERENCES public.company_inventory(source_system, source_entity_id) ON DELETE CASCADE,
    CHECK ((outcome = 'FAILED') = (failure_kind IS NOT NULL)),
    CHECK ((outcome = 'FAILED') = (response_sha256 IS NULL))
);
